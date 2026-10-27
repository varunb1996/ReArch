"""Java, C# and PHP: extract -> link on small hand-written repos with known answers.

Each repo is written to a temp dir so the expected edges sit next to the code
that should produce them. Negative cases matter as much as positive ones: the
linker must leave an edge unresolved rather than guess.
"""
import json
from pathlib import Path

from parser.pipeline.extract_symbols import main as extract_symbols
from parser.pipeline.link_calls import main as link_calls


def build_graph(tmp_path: Path, files: dict[str, str]) -> dict:
    repo = tmp_path / "repo"
    for rel, source in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    extract_symbols(str(repo), str(tmp_path / "symbols"))
    link_calls(str(tmp_path / "symbols"), str(tmp_path / "graph.json"))
    return json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))


def edge_set(graph: dict, kind: str, resolution: str | None = None) -> set[tuple[str, str]]:
    return {
        (e["source"], e["target"]) for e in graph["edges"]
        if e["kind"] == kind and (resolution is None or e["resolution"] == resolution)
    }


def external_of(graph: dict, target: str) -> dict | None:
    return next((e.get("external") for e in graph["edges"] if e["target"] == target), None)


# --------------------------------------------------------------------- Java

JAVA = {
    "src/main/java/com/acme/core/Util.java": """
package com.acme.core;
public class Util { public static void log(String m) {} }
""",
    "src/main/java/com/acme/core/BaseService.java": """
package com.acme.core;
public abstract class BaseService { public void audit(String s) {} public void place() {} }
""",
    "src/main/java/com/acme/shop/PaymentGateway.java": """
package com.acme.shop;
public interface PaymentGateway { void charge(int amount); }
""",
    "src/main/java/com/acme/shop/Invoice.java": """
package com.acme.shop;
public class Invoice { public Invoice(Object o) {} public int total() { return 1; } }
""",
    "src/main/java/com/acme/shop/OrderService.java": """
package com.acme.shop;
import com.acme.core.*;
import org.springframework.stereotype.Service;
import java.util.List;
public class OrderService extends BaseService {
    private final PaymentGateway gateway;
    private List<Invoice> invoices;
    public OrderService(PaymentGateway gateway) { this.gateway = gateway; }
    public void place(int n) {
        Invoice inv = new Invoice(n);
        gateway.charge(inv.total());
        this.audit("x");
        Util.log("a");
        helper();
        invoices.add(inv);
        super.place();
        mystery.run();
    }
    private void helper() {}
    public void helper(int overloaded) {}
}
""",
}


def test_java_calls_resolve_through_declared_types(tmp_path):
    graph = build_graph(tmp_path, JAVA)
    place = "java:src/main/java/com/acme/shop/OrderService.java:OrderService.place"
    resolved = edge_set(graph, "calls", "resolved")
    assert (place, "java:src/main/java/com/acme/shop/Invoice.java:Invoice") in resolved               # new Invoice()
    assert (place, "java:src/main/java/com/acme/shop/Invoice.java:Invoice.total") in resolved         # local typed `Invoice inv`
    assert (place, "java:src/main/java/com/acme/shop/PaymentGateway.java:PaymentGateway.charge") in resolved  # interface-typed field
    assert (place, "java:src/main/java/com/acme/core/BaseService.java:BaseService.audit") in resolved  # this.audit via base class
    assert (place, "java:src/main/java/com/acme/core/Util.java:Util.log") in resolved                 # static call, wildcard import
    assert (place, "java:src/main/java/com/acme/shop/OrderService.java:OrderService.helper") in resolved  # bare call
    assert (place, "java:src/main/java/com/acme/core/BaseService.java:BaseService.place") in resolved # super.place()


def test_java_inheritance_and_unknowns(tmp_path):
    graph = build_graph(tmp_path, JAVA)
    assert (
        "java:src/main/java/com/acme/shop/OrderService.java:OrderService",
        "java:src/main/java/com/acme/core/BaseService.java:BaseService",
    ) in edge_set(graph, "inherits", "resolved")
    place = "java:src/main/java/com/acme/shop/OrderService.java:OrderService.place"
    # An undeclared receiver must not be guessed at.
    assert (place, "unresolved:mystery.run") in edge_set(graph, "calls", "unresolved")
    assert not any(e["source"] == place and e["target"].endswith(":mystery.run") and e["resolution"] == "resolved" for e in graph["edges"])


def test_java_external_dependencies_are_classified(tmp_path):
    graph = build_graph(tmp_path, JAVA)
    assert external_of(graph, "unresolved:org.springframework.stereotype.Service") == {"package": "org.springframework", "kind": "third-party"}
    assert external_of(graph, "unresolved:java.util.List") == {"package": "java.util", "kind": "stdlib"}
    # `invoices.add` is a call on a java.util.List-typed field: stdlib, not project code
    assert external_of(graph, "unresolved:invoices.add") == {"package": "java.util", "kind": "stdlib"}


def test_java_overloads_get_separate_nodes_with_unique_ids(tmp_path):
    graph = build_graph(tmp_path, JAVA)
    ids = [n["id"] for n in graph["nodes"]]
    assert len(ids) == len(set(ids))
    helpers = [n for n in graph["nodes"] if n["qualified_name"] == "OrderService.helper"]
    assert len(helpers) == 2


def test_java_static_import_depends_on_its_class(tmp_path):
    files = {
        "a/Helpers.java": "package a;\npublic class Helpers { public static int twice(int x) { return x * 2; } }\n",
        "b/User.java": "package b;\nimport static a.Helpers.twice;\npublic class User { void go() { twice(2); } }\n",
    }
    graph = build_graph(tmp_path, files)
    assert ("java:b/User.java:<module>", "java:a/Helpers.java:Helpers") in edge_set(graph, "imports", "resolved")


# --------------------------------------------------------------------- C#

CSHARP = {
    "Core/Util.cs": """
namespace Acme.Core { public static class Util { public static void Log(string m) {} } }
""",
    "Core/BaseService.cs": """
namespace Acme.Core
{
    public class BaseService { public void Audit(string s) {} public virtual void Place(object o) {} }
}
""",
    "Shop/IPaymentGateway.cs": """
namespace Acme.Shop.Orders;
public interface IPaymentGateway { void Charge(int amount); }
""",
    "Shop/Invoice.cs": """
namespace Acme.Shop.Orders;
public class Invoice { public Invoice(object o) {} public int Total() => 1; }
""",
    "Shop/OrderService.cs": """
using System;
using System.Linq;
using Acme.Core;
using Json = Newtonsoft.Json.JsonConvert;
namespace Acme.Shop.Orders
{
    public class OrderService : BaseService
    {
        private readonly IPaymentGateway _gateway;
        public Invoice Last { get; set; }
        public OrderService(IPaymentGateway gateway) { _gateway = gateway; }
        public void Place(object o)
        {
            var inv = new Invoice(o);
            _gateway.Charge(inv.Total());
            this.Audit("x");
            Util.Log("a");
            Helper();
            base.Place(o);
            Last.Total();
            Json.Serialize(o);
            Console.WriteLine("a");
            mystery.Run();
        }
        private void Helper() {}
    }
    public partial class Split { public void A() { B(); } }
}
""",
    "Shop/SplitB.cs": """
namespace Acme.Shop.Orders { public partial class Split { public void B() {} } }
""",
}


def test_csharp_calls_resolve_through_namespaces_and_types(tmp_path):
    graph = build_graph(tmp_path, CSHARP)
    place = "csharp:Shop/OrderService.cs:OrderService.Place"
    resolved = edge_set(graph, "calls", "resolved")
    assert (place, "csharp:Shop/Invoice.cs:Invoice") in resolved                          # new Invoice(), same namespace, other file
    assert (place, "csharp:Shop/Invoice.cs:Invoice.Total") in resolved                    # `var inv = new Invoice()`
    assert (place, "csharp:Shop/IPaymentGateway.cs:IPaymentGateway.Charge") in resolved   # interface-typed field _gateway
    assert (place, "csharp:Core/BaseService.cs:BaseService.Audit") in resolved            # this.Audit via base
    assert (place, "csharp:Core/Util.cs:Util.Log") in resolved                            # `using Acme.Core;`
    assert (place, "csharp:Shop/OrderService.cs:OrderService.Helper") in resolved
    assert (place, "csharp:Core/BaseService.cs:BaseService.Place") in resolved            # base.Place
    assert (place, "csharp:Shop/Invoice.cs:Invoice.Total") in resolved                    # property of type Invoice


def test_csharp_partial_classes_share_methods(tmp_path):
    graph = build_graph(tmp_path, CSHARP)
    assert (
        "csharp:Shop/OrderService.cs:Split.A",
        "csharp:Shop/SplitB.cs:Split.B",
    ) in edge_set(graph, "calls", "resolved")


def test_csharp_inheritance_externals_and_unknowns(tmp_path):
    graph = build_graph(tmp_path, CSHARP)
    assert ("csharp:Shop/OrderService.cs:OrderService", "csharp:Core/BaseService.cs:BaseService") in edge_set(graph, "inherits", "resolved")
    assert external_of(graph, "unresolved:Json.Serialize") == {"package": "Newtonsoft", "kind": "third-party"}
    assert external_of(graph, "unresolved:Console.WriteLine") == {"package": "System", "kind": "builtin"}
    assert external_of(graph, "unresolved:System.Linq") == {"package": "System", "kind": "stdlib"}
    assert ("csharp:Shop/OrderService.cs:OrderService.Place", "unresolved:mystery.Run") in edge_set(graph, "calls", "unresolved")


# --------------------------------------------------------------------- PHP

PHP = {
    "src/Core/Util.php": "<?php\nnamespace App\\Core;\nclass Util { public static function log($m) {} }\n",
    "src/Core/BaseService.php": "<?php\nnamespace App\\Core;\nclass BaseService { public function audit($s) {} public function place($o) {} }\n",
    "src/Core/Loggable.php": "<?php\nnamespace App\\Core;\ntrait Loggable { public function note($s) {} }\n",
    "src/Shop/PaymentGateway.php": "<?php\nnamespace App\\Shop;\ninterface PaymentGateway { public function charge($amount); }\n",
    "src/Shop/Cache.php": "<?php\nnamespace App\\Shop;\nclass Cache { public function get($k) {} }\n",
    "src/Shop/Invoice.php": "<?php\nnamespace App\\Shop;\nclass Invoice { public function __construct($o) {} public function total() {} }\n",
    "src/Shop/OrderService.php": """<?php
namespace App\\Shop;

use App\\Core\\{BaseService, Util as U, Loggable};
use Vendor\\Pkg\\Thing;
use Illuminate\\Support\\Collection;

class OrderService extends BaseService
{
    use Loggable;
    private $gateway;
    public function __construct(private Cache $cache, PaymentGateway $gateway) { $this->gateway = $gateway; }
    public function place(Invoice $o): void
    {
        $inv = new Invoice($o);
        $this->gateway->charge($inv->total());
        $this->cache->get(1);
        $this->audit("x");
        $this->note("n");
        U::log("a");
        self::helper();
        parent::place($o);
        strlen("x");
        \\App\\Core\\Util::log("fq");
        $unknown->run();
        $t = new Thing();
        helperFn();
    }
    private static function helper() {}
}
function helperFn() {}
""",
}


def test_php_calls_resolve_through_use_statements_and_property_types(tmp_path):
    graph = build_graph(tmp_path, PHP)
    place = "php:src/Shop/OrderService.php:OrderService.place"
    resolved = edge_set(graph, "calls", "resolved")
    assert (place, "php:src/Shop/Invoice.php:Invoice") in resolved                         # new Invoice, same namespace
    assert (place, "php:src/Shop/Invoice.php:Invoice.total") in resolved                   # `$inv = new Invoice()`
    assert (place, "php:src/Shop/PaymentGateway.php:PaymentGateway.charge") in resolved    # property typed from typed ctor param
    assert (place, "php:src/Shop/Cache.php:Cache.get") in resolved                         # promoted constructor property
    assert (place, "php:src/Core/BaseService.php:BaseService.audit") in resolved           # $this->audit via group `use`
    assert (place, "php:src/Core/Loggable.php:Loggable.note") in resolved                  # trait method
    assert (place, "php:src/Core/Util.php:Util.log") in resolved                           # aliased `Util as U`
    assert (place, "php:src/Shop/OrderService.php:OrderService.helper") in resolved        # self::helper
    assert (place, "php:src/Core/BaseService.php:BaseService.place") in resolved           # parent::place
    assert (place, "php:src/Shop/OrderService.php:helperFn") in resolved                   # namespaced function


def test_php_inheritance_externals_and_unknowns(tmp_path):
    graph = build_graph(tmp_path, PHP)
    assert ("php:src/Shop/OrderService.php:OrderService", "php:src/Core/BaseService.php:BaseService") in edge_set(graph, "inherits", "resolved")
    assert external_of(graph, "unresolved:Vendor.Pkg.Thing") == {"package": "Vendor", "kind": "third-party"}
    assert external_of(graph, "unresolved:Illuminate.Support.Collection") == {"package": "Illuminate", "kind": "third-party"}
    assert external_of(graph, "unresolved:strlen") == {"package": "php-builtins", "kind": "builtin"}
    assert ("php:src/Shop/OrderService.php:OrderService.place", "unresolved:unknown.run") in edge_set(graph, "calls", "unresolved")


# ------------------------------------------------------------- shared rules

def test_declared_types_add_reference_edges_even_without_calls(tmp_path):
    files = {
        "a/Repo.java": "package a;\npublic class Repo {}\n",
        "b/Holder.java": "package b;\nimport a.Repo;\npublic class Holder { private Repo repo; }\n",
    }
    graph = build_graph(tmp_path, files)
    assert ("java:b/Holder.java:<module>", "java:a/Repo.java:Repo") in edge_set(graph, "imports", "resolved")


def test_languages_do_not_link_to_each_other(tmp_path):
    files = {
        "a/Util.java": "package a;\npublic class Util { public static void log() {} }\n",
        "b/Caller.cs": "namespace b { public class Caller { void Go() { Util.log(); } } }\n",
    }
    graph = build_graph(tmp_path, files)
    assert not any(e["resolution"] == "resolved" and e["source"].startswith("csharp:") and e["target"].startswith("java:") for e in graph["edges"])


def test_test_projects_and_build_output_are_not_analyzed(tmp_path):
    files = {
        "src/Main.java": "package m;\npublic class Main {}\n",
        "src/MainTest.java": "package m;\npublic class MainTest {}\n",
        "src/test/java/Helper.java": "package m;\npublic class Helper {}\n",
        "Svc/Svc.cs": "namespace Svc { public class Svc {} }\n",
        "Svc.Tests/SvcTests.cs": "namespace Svc { public class SvcTests {} }\n",
        "Svc/obj/Debug/Gen.cs": "namespace Svc { public class Gen {} }\n",
        "Svc/Form.Designer.cs": "namespace Svc { public class FormGen {} }\n",
        "vendor/lib/Lib.php": "<?php\nnamespace V;\nclass Lib {}\n",
        "app/App.php": "<?php\nnamespace A;\nclass App {}\n",
    }
    graph = build_graph(tmp_path, files)
    classes = {n["name"] for n in graph["nodes"] if n["kind"] == "class"}
    assert classes == {"Main", "Svc", "App"}


# ------------------------------------------------ regressions found on real repos

def test_string_and_object_are_real_types_not_primitives(tmp_path):
    """`String s; s.charAt(0)` once lost its type (String was filtered like `string`), leaving the call unclassified."""
    files = {"a/A.java": "package a;\npublic class A { int f(final String s, Object o) { o.hashCode(); return s.length(); } }\n"}
    graph = build_graph(tmp_path, files)
    assert external_of(graph, "unresolved:s.length") == {"package": "java.lang", "kind": "builtin"}
    assert external_of(graph, "unresolved:o.hashCode") == {"package": "java.lang", "kind": "builtin"}


def test_calls_in_field_initializers_resolve_against_their_class(tmp_path):
    files = {"a/A.java": "package a;\npublic class A { static final int X = compute();\n static int compute() { return 1; } }\n"}
    graph = build_graph(tmp_path, files)
    assert ("java:a/A.java:A", "java:a/A.java:A.compute") in edge_set(graph, "calls", "resolved")


def test_var_declared_from_a_call_takes_the_methods_declared_return_type(tmp_path):
    files = {
        "Domain/Basket.cs": "namespace Shop.Domain { public class Basket { public void AddItem(int id) {} } }\n",
        "Domain/IBaskets.cs": "using System.Threading.Tasks;\nnamespace Shop.Domain { public interface IBaskets { Task<Basket> GetAsync(int id); Basket Other(int a); Basket Other(string a); } }\n",
        "Svc.cs": """using Shop.Domain;
namespace Shop {
  public class Svc {
    private readonly IBaskets _baskets;
    public async System.Threading.Tasks.Task Go() {
        var basket = await _baskets.GetAsync(1);
        basket.AddItem(2);
        var ambiguous = _baskets.Other(1);
        ambiguous.AddItem(3);
    }
  }
}
""",
    }
    graph = build_graph(tmp_path, files)
    go = "csharp:Svc.cs:Svc.Go"
    calls = [(e["target"], e["resolution"]) for e in graph["edges"] if e["source"] == go and e["kind"] == "calls"]
    # Task<Basket> is looked through: `basket` is a Basket.
    assert ("csharp:Domain/Basket.cs:Basket.AddItem", "resolved") in calls
    # `Other` is overloaded, so its return type is not known from the name alone: no guessing.
    assert ("unresolved:ambiguous.AddItem", "unresolved") in calls
    assert len([t for t, r in calls if t == "csharp:Domain/Basket.cs:Basket.AddItem"]) == 1


def test_static_field_and_field_chains_resolve_one_hop_at_a_time(tmp_path):
    files = {
        "p/Strings.java": "package p;\npublic class Strings { public static final Strings CS = new Strings(); public String once(String a) { return a; } }\n",
        "p/Client.java": "package p;\npublic class Client { public void send() {} }\n",
        "p/Gateway.java": "package p;\npublic class Gateway { Client client; }\n",
        "p/User.java": "package p;\npublic class User { Gateway gateway;\n void go() { Strings.CS.once(\"x\"); this.gateway.client.send(); } }\n",
    }
    graph = build_graph(tmp_path, files)
    resolved = edge_set(graph, "calls", "resolved")
    assert ("java:p/User.java:User.go", "java:p/Strings.java:Strings.once") in resolved
    assert ("java:p/User.java:User.go", "java:p/Client.java:Client.send") in resolved


def test_lambda_variables_and_fluent_chains_never_become_fake_packages(tmp_path):
    files = {
        "A.cs": "namespace N { public class A { void F(System.Collections.Generic.List<int> xs) { xs.Select(b => b.Id.ToString()); new B().Add(1).Add(2); } } }\n",
    }
    graph = build_graph(tmp_path, files)
    packages = {e["external"]["package"] for e in graph["edges"] if e.get("external") and e["external"]["kind"] == "third-party"}
    assert packages == set()


def test_large_hand_written_classes_are_not_skipped(tmp_path):
    body = "\n".join(f"    public static int m{i}(int x) {{ return x + {i}; }}" for i in range(9000))
    files = {"p/Big.java": f"package p;\npublic class Big {{\n{body}\n}}\n"}  # > 400 KB, the limit that suits minified JS
    assert len(files["p/Big.java"]) > 400_000
    graph = build_graph(tmp_path, files)
    assert any(n["name"] == "Big" for n in graph["nodes"])


def test_symbol_file_names_stay_short_unique_and_in_path_order(tmp_path):
    """The names once embedded the whole repo path, which broke on deep Java trees (OS path limits)."""
    repo = tmp_path / "repo"
    files = {
        "a/very/deep/package/tree/" * 3 + "Util.java": "package a;\npublic class Util {}\n",
        "b/Util.java": "package b;\npublic class Util {}\n",   # same file name, different directory
        "c/" + "L" * 90 + ".java": "package c;\npublic class L {}\n",
    }
    for rel, source in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(source, encoding="utf-8")
    extract_symbols(str(repo), str(tmp_path / "symbols"))
    names = sorted(f.name for f in (tmp_path / "symbols").iterdir())
    assert len(names) == 3 and len(set(names)) == 3
    assert max(len(n) for n in names) < 100  # the 90-character file name was truncated
    loaded = [json.loads((tmp_path / "symbols" / n).read_text(encoding="utf-8"))["path"] for n in names]
    assert loaded == sorted(loaded)  # processing order == path order, so graphs stay deterministic
