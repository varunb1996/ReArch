// Deliberately tiny markdown renderer for the LLM-written plan: headings,
// bullets, numbered items, **bold** and `code`. It builds React elements and
// never injects HTML, so model output cannot smuggle markup into the page.

function inline(text, prefix) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    const key = `${prefix}-${i}`;
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) return <strong key={key}>{part.slice(2, -2)}</strong>;
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) return <code key={key}>{part.slice(1, -1)}</code>;
    return part;
  });
}

export default function Markdown({ text }) {
  const blocks = [];
  let paragraph = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ type: "p", text: paragraph.join(" ") });
    paragraph = [];
  };

  for (const raw of text.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    const item = line.match(/^(\s*)(?:[-*]|\d+\.)\s+(.*)$/);
    if (!line.trim()) flush();
    else if (heading) { flush(); blocks.push({ type: "h", text: heading[1] }); }
    else if (item) { flush(); blocks.push({ type: "li", depth: Math.floor(item[1].length / 2), text: item[2] }); }
    else paragraph.push(line.trim());
  }
  flush();

  return (
    <div className="md">
      {blocks.map((b, i) => {
        if (b.type === "h") return <h4 key={i}>{inline(b.text, i)}</h4>;
        if (b.type === "li") return <div key={i} className="md-li" style={{ paddingLeft: 14 + b.depth * 16 }}>{inline(b.text, i)}</div>;
        return <p key={i}>{inline(b.text, i)}</p>;
      })}
    </div>
  );
}
