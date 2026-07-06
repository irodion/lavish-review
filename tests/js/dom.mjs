// A minimal, dependency-free DOM for exercising the vendored cockpit script under
// Node (issue #67 — the repo's first JS test harness). It implements exactly the
// slice of the DOM the cockpit's app.js touches: element creation, text content,
// a small CSS-selector engine (type / #id / .class / [attr], [attr^=], [attr$=],
// [attr*=], [attr=] with descendant combinators and comma groups), class lists,
// datasets, deep cloning, and a capture→bubble event model. It is deliberately
// NOT a general browser: there is no HTML string parsing and no innerHTML sink, so
// the only way markup enters the tree is createElement + textContent — which is
// precisely the discipline the cockpit itself follows, letting the tests prove that
// a hostile diff can only ever render as text.

let idCounter = 0;

// Void elements never take children; nothing here needs them, but keeping the set
// makes the tag handling explicit.
const RAW_TEXT = new Set(["SCRIPT", "STYLE", "TEXTAREA", "TITLE"]);

function toDatasetKey(prop) {
  // `dataset.fooBar` ⇄ `data-foo-bar`, matching the browser's camel⇄kebab mapping.
  return "data-" + prop.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());
}

class Node {
  constructor() {
    this.childNodes = [];
    this.parentNode = null;
  }

  get parentElement() {
    return this.parentNode instanceof Element ? this.parentNode : null;
  }

  get firstChild() {
    return this.childNodes[0] || null;
  }

  get nextSibling() {
    const siblings = this.parentNode ? this.parentNode.childNodes : null;
    if (!siblings) return null;
    const i = siblings.indexOf(this);
    return i === -1 ? null : siblings[i + 1] || null;
  }

  appendChild(node) {
    node.remove();
    node.parentNode = this;
    this.childNodes.push(node);
    return node;
  }

  insertBefore(node, ref) {
    if (ref == null) return this.appendChild(node);
    const i = this.childNodes.indexOf(ref);
    if (i === -1) return this.appendChild(node);
    node.remove();
    node.parentNode = this;
    this.childNodes.splice(i, 0, node);
    return node;
  }

  removeChild(node) {
    const i = this.childNodes.indexOf(node);
    if (i !== -1) {
      this.childNodes.splice(i, 1);
      node.parentNode = null;
    }
    return node;
  }

  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }
}

class TextNode extends Node {
  constructor(data) {
    super();
    this.nodeType = 3;
    this.data = String(data);
  }

  get textContent() {
    return this.data;
  }

  set textContent(value) {
    this.data = String(value);
  }

  cloneNode() {
    return new TextNode(this.data);
  }
}

class EventTargetBase extends Node {
  constructor() {
    super();
    this._listeners = Object.create(null);
  }

  addEventListener(type, fn, options) {
    const capture = options === true || !!(options && options.capture === true);
    (this._listeners[type] = this._listeners[type] || []).push({ fn, capture });
  }

  removeEventListener(type, fn) {
    const list = this._listeners[type];
    if (list) this._listeners[type] = list.filter((l) => l.fn !== fn);
  }

  _runListeners(event, capture) {
    const list = this._listeners[event.type];
    if (!list) return;
    event.currentTarget = this;
    for (const l of list.slice()) {
      if (event._immediate) break;
      if (l.capture === capture) l.fn.call(this, event);
    }
  }

  dispatchEvent(event) {
    event.target = this;
    // Ancestors only — the target is handled in its own phase below.
    const path = [];
    for (let n = this.parentNode; n; n = n.parentNode) path.push(n);
    // Capture phase: root → parent.
    for (let i = path.length - 1; i >= 0 && !event._stopped; i--) {
      path[i]._runListeners(event, true);
    }
    // Target phase: both capture- and bubble-registered listeners on the target
    // fire regardless of `bubbles` (stopPropagation only affects other nodes).
    if (!event._immediate) this._runListeners(event, true);
    if (!event._immediate) this._runListeners(event, false);
    // Bubble phase: parent → root, only when the event bubbles.
    if (event.bubbles) {
      for (let i = 0; i < path.length && !event._stopped; i++) {
        path[i]._runListeners(event, false);
      }
    }
    event.currentTarget = null;
    return !event.defaultPrevented;
  }
}

class Element extends EventTargetBase {
  constructor(tag, ownerDocument) {
    super();
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.ownerDocument = ownerDocument;
    this._attrs = Object.create(null);
  }

  // --- attributes -----------------------------------------------------------

  setAttribute(name, value) {
    this._attrs[String(name).toLowerCase()] = String(value);
  }

  getAttribute(name) {
    const key = String(name).toLowerCase();
    return key in this._attrs ? this._attrs[key] : null;
  }

  hasAttribute(name) {
    return String(name).toLowerCase() in this._attrs;
  }

  removeAttribute(name) {
    delete this._attrs[String(name).toLowerCase()];
  }

  get id() {
    return this._attrs.id || "";
  }

  set id(value) {
    this._attrs.id = String(value);
  }

  get className() {
    return this._attrs.class || "";
  }

  set className(value) {
    this._attrs.class = String(value);
  }

  get classList() {
    const el = this;
    const read = () => (el._attrs.class || "").split(/\s+/).filter(Boolean);
    const write = (list) => {
      el._attrs.class = list.join(" ");
    };
    return {
      contains: (name) => read().includes(name),
      add: (...names) => {
        const list = read();
        for (const n of names) if (!list.includes(n)) list.push(n);
        write(list);
      },
      remove: (...names) => {
        write(read().filter((c) => !names.includes(c)));
      },
      toggle: (name, force) => {
        const list = read();
        const has = list.includes(name);
        const shouldHave = force === undefined ? !has : force;
        if (shouldHave && !has) list.push(name);
        else if (!shouldHave && has) list.splice(list.indexOf(name), 1);
        write(list);
        return shouldHave;
      },
    };
  }

  get dataset() {
    const el = this;
    return new Proxy(
      {},
      {
        get: (_t, prop) =>
          typeof prop === "string" ? el.getAttribute(toDatasetKey(prop)) ?? undefined : undefined,
        set: (_t, prop, value) => {
          el.setAttribute(toDatasetKey(prop), value);
          return true;
        },
        has: (_t, prop) => el.hasAttribute(toDatasetKey(prop)),
      }
    );
  }

  // <details open> — mirror the boolean attribute so `.open = true` and the
  // `[open]` selector agree, exactly as the browser does.
  get open() {
    return this.hasAttribute("open");
  }

  set open(value) {
    if (value) this.setAttribute("open", "");
    else this.removeAttribute("open");
  }

  // A few reflected convenience properties the script assigns to.
  set colSpan(v) {
    this.setAttribute("colspan", String(v));
  }
  set type(v) {
    this.setAttribute("type", String(v));
  }
  set rows(v) {
    this.setAttribute("rows", String(v));
  }
  set href(v) {
    this.setAttribute("href", String(v));
  }
  get href() {
    return this.getAttribute("href") || "";
  }
  // <input>/<textarea> value is state, not an attribute — a plain field is enough.
  get value() {
    return this._value || "";
  }
  set value(v) {
    this._value = String(v);
  }

  // --- children / text ------------------------------------------------------

  get children() {
    return this.childNodes.filter((n) => n instanceof Element);
  }

  get textContent() {
    let out = "";
    for (const n of this.childNodes) out += n.textContent;
    return out;
  }

  set textContent(value) {
    this.childNodes = [];
    if (value !== "" && value != null) {
      this.appendChild(new TextNode(value));
    }
  }

  cloneNode(deep) {
    const copy = new Element(this.tagName, this.ownerDocument);
    copy._attrs = { ...this._attrs };
    if (this._value !== undefined) copy._value = this._value;
    if (deep) {
      for (const child of this.childNodes) copy.appendChild(child.cloneNode(true));
    }
    return copy;
  }

  // --- selectors ------------------------------------------------------------

  matches(selector) {
    return matchesGroup(this, selector);
  }

  closest(selector) {
    for (let n = this; n instanceof Element; n = n.parentNode) {
      if (n.matches(selector)) return n;
    }
    return null;
  }

  querySelector(selector) {
    for (const el of this._descendants()) {
      if (matchesGroup(el, selector)) return el;
    }
    return null;
  }

  querySelectorAll(selector) {
    const out = [];
    for (const el of this._descendants()) {
      if (matchesGroup(el, selector)) out.push(el);
    }
    return out;
  }

  *_descendants() {
    for (const child of this.childNodes) {
      if (child instanceof Element) {
        yield child;
        yield* child._descendants();
      }
    }
  }
}

class Document extends EventTargetBase {
  constructor() {
    super();
    this.nodeType = 9;
    this.ownerDocument = this;
    this.documentElement = this.createElement("html");
    this.body = this.createElement("body");
    this.documentElement.appendChild(this.body);
    this.appendChild(this.documentElement);
  }

  createElement(tag) {
    return new Element(tag, this);
  }

  createTextNode(data) {
    return new TextNode(data);
  }

  getElementById(id) {
    for (const el of this.documentElement._descendants()) {
      if (el.id === id) return el;
    }
    return null;
  }

  querySelector(selector) {
    return this.documentElement.matches(selector)
      ? this.documentElement
      : this.documentElement.querySelector(selector);
  }

  querySelectorAll(selector) {
    const out = [];
    if (this.documentElement.matches(selector)) out.push(this.documentElement);
    for (const el of this.documentElement._descendants()) {
      if (matchesGroup(el, selector)) out.push(el);
    }
    return out;
  }
}

// --- selector engine ---------------------------------------------------------

function parseCompound(text) {
  const compound = { tag: null, id: null, classes: [], attrs: [] };
  const re = /([.#]?[\w-]+)|\[([\w-]+)(?:([~^$*]?=)"?([^"\]]*)"?)?\]/g;
  let m;
  while ((m = re.exec(text))) {
    if (m[1]) {
      const token = m[1];
      if (token[0] === ".") compound.classes.push(token.slice(1));
      else if (token[0] === "#") compound.id = token.slice(1);
      else compound.tag = token.toUpperCase();
    } else {
      compound.attrs.push({ name: m[2].toLowerCase(), op: m[3] || null, value: m[4] || "" });
    }
  }
  return compound;
}

function matchesCompound(el, compound) {
  if (compound.tag && el.tagName !== compound.tag) return false;
  if (compound.id && el.id !== compound.id) return false;
  for (const cls of compound.classes) {
    if (!el.classList.contains(cls)) return false;
  }
  for (const attr of compound.attrs) {
    if (!el.hasAttribute(attr.name)) return false;
    if (!attr.op) continue;
    const actual = el.getAttribute(attr.name) || "";
    if (attr.op === "=" && actual !== attr.value) return false;
    if (attr.op === "^=" && !actual.startsWith(attr.value)) return false;
    if (attr.op === "$=" && !actual.endsWith(attr.value)) return false;
    if (attr.op === "*=" && !actual.includes(attr.value)) return false;
    if (attr.op === "~=" && !actual.split(/\s+/).includes(attr.value)) return false;
  }
  return true;
}

// A complex selector is a chain of compounds joined by the descendant combinator
// (whitespace). Match right-to-left: the element must match the rightmost compound
// and each preceding compound must match some ancestor, in order.
function matchesComplex(el, compounds) {
  let i = compounds.length - 1;
  if (!matchesCompound(el, compounds[i])) return false;
  i--;
  let ancestor = el.parentNode;
  while (i >= 0) {
    if (ancestor instanceof Element && matchesCompound(ancestor, compounds[i])) {
      i--;
    }
    ancestor = ancestor ? ancestor.parentNode : null;
    if (!ancestor && i >= 0) return false;
  }
  return true;
}

function matchesGroup(el, selector) {
  for (const complex of selector.split(",")) {
    const compounds = complex.trim().split(/\s+/).map(parseCompound);
    if (matchesComplex(el, compounds)) return true;
  }
  return false;
}

// --- events ------------------------------------------------------------------

class DomEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.bubbles = options.bubbles !== false;
    this.target = null;
    this.currentTarget = null;
    this.defaultPrevented = false;
    this._stopped = false;
    this._immediate = false;
  }

  preventDefault() {
    this.defaultPrevented = true;
  }

  stopPropagation() {
    this._stopped = true;
  }

  stopImmediatePropagation() {
    this._stopped = true;
    this._immediate = true;
  }
}

export { Document, Element, TextNode, DomEvent };
export const __id = () => ++idCounter;
