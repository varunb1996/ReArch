function onClick(event) {
  return `clicked ${event}`;
}

function onHover(event) {
  return `hovered ${event}`;
}

const handlers = {
  click: onClick,
  hover: onHover,
};

export function dispatch(name, event) {
  // Dynamic/ambiguous call site, mirrors backend/dispatch.py: the target
  // depends on the runtime value of `name`, so this must resolve to a
  // low-confidence fan-out edge across every value in `handlers`.
  return handlers[name](event);
}
