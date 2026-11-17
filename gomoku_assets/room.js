"use strict";

const byId = (id) => document.getElementById(id);
const ui = Object.fromEntries(Array.from(document.querySelectorAll("[id]"), (element) => [element.id, element]));
const roomPath = location.pathname;
const retained = new Map();
let socket, state, member, marker, requested;
let snapshotAt = performance.now();
let intentionalClose = false;

function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}

function update(key, value, render) {
  const encoded = JSON.stringify(value);
  if (retained.get(key) !== encoded) {
    retained.set(key, encoded);
    render();
  }
}

function transmit(type, fields = {}) {
  if (socket?.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify({type, ...fields}));
  return true;
}

function notice(text = "") {
  ui.notice.textContent = text;
}

const cells = Array.from({length: 225}, (_, index) => {
  const button = node("button", undefined, "cell");
  button.type = "button";
  const row = Math.floor(index / 15), col = index % 15;
  button.setAttribute("aria-label", `${row + 1}행 ${col + 1}열`);
  if ([3, 7, 11].includes(row) && [3, 7, 11].includes(col)) button.classList.add("star");
  button.addEventListener("click", () => {
    if (state?.turn !== member || state.board[index]) return;
    notice();
    if (marker === index) {
      requested = undefined;
      transmit("move", {index});
    } else if (transmit("preview", {index})) {
      requested = index;
      marker = undefined;
      renderBoard();
    }
  });
  return button;
});
ui.board.append(...cells);

function renderBoard() {
  cells.forEach((cell, index) => {
    const stone = state.board[index];
    if (stone) cell.dataset.stone = stone;
    else delete cell.dataset.stone;
    cell.classList.toggle("preview", index === marker && !stone);
    cell.tabIndex = state.turn === member && !stone ? 0 : -1;
  });
}

function renderPeople() {
  ui.players.replaceChildren(...state.players.map((player) => {
    const card = node("div", undefined, `person${state.turn === player.id ? " active" : ""}`);
    const detail = node("div");
    const record = player.standing ?? {games: 0, wins: 0, draws: 0, losses: 0};
    detail.append(node("strong", player.name), node("small", `${record.games}전 ${record.wins}승 ${record.draws}무 ${record.losses}패`));
    if (player.color) card.append(node("span", undefined, `stone ${player.color === 1 ? "black" : "white"}`));
    card.append(detail);
    if (state.mode === 2 && state.phase !== "waiting") {
      const timer = node("time", "00:00.00");
      timer.dataset.clock = player.id;
      card.append(timer);
    }
    return card;
  }));
  byId("spectator-count").textContent = state.spectators.length;
  byId("spectator-heading").textContent = `관전자 ${state.spectators.length}명`;
  ui.spectators.replaceChildren(...state.spectators.map(({name}) => node("li", name)));
}

function renderMessages() {
  const nearEnd = ui.messages.scrollHeight - ui.messages.scrollTop - ui.messages.clientHeight < 45;
  ui.messages.replaceChildren(...state.messages.map(({member: sender, text}) => {
    const block = node("div", undefined, "message");
    if (sender.avatar) {
      const avatar = node("img");
      avatar.src = sender.avatar;
      avatar.alt = "";
      block.append(avatar);
    }
    const body = node("div");
    body.append(node("strong", sender.name), node("p", text));
    block.append(body);
    return block;
  }));
  if (nearEnd) ui.messages.scrollTop = ui.messages.scrollHeight;
}

function renderLobby(me) {
  ui.versus.replaceChildren(node("span", state.players[0]?.name || "대기 중"),
    node("span", "VS"), node("span", state.players[1]?.name || "대기 중"));
  byId("normal-mode").setAttribute("aria-pressed", String(state.mode === 1));
  byId("blitz-mode").setAttribute("aria-pressed", String(state.mode === 2));
  const seconds = state.time_options[state.mode];
  ui.seconds.replaceChildren(...seconds.map((value) => {
    const option = node("option", state.mode === 1 ? `${value}초` : `${value / 60}분`);
    option.value = value;
    return option;
  }));
  ui.seconds.value = state.seconds;
  ui.black.setAttribute("aria-pressed", String(me?.preference === 1));
  ui.white.setAttribute("aria-pressed", String(me?.preference === 2));
  ui.ready.textContent = me?.ready ? "준비 취소" : "준비";
  ui.ready.setAttribute("aria-pressed", String(Boolean(me?.ready)));
}

function renderResult() {
  const result = state.result;
  const winner = state.players.find(({id}) => id === result.winner);
  const reasons = {five: "오목", timeout: "시간패", resignation: "기권", "no-legal-move": "합법 착수 없음"};
  const exit = node("button", "나가기");
  exit.type = "button";
  exit.addEventListener("click", leave);
  ui.result.replaceChildren(node("p", `${winner ? winner.name + " 승리" : "무승부"} · ${reasons[result.reason]}`), exit);
}

function applyRoom(next) {
  if (state?.turn !== next.turn || next.board[marker]) {
    marker = requested = undefined;
    notice();
  }
  state = next;
  snapshotAt = performance.now();
  if (state.phase === "closed") return finish("방이 종료되었습니다.");
  const waiting = state.phase === "waiting";
  const me = state.players.find(({id}) => id === member);
  const current = state.players.find(({id}) => id === state.turn);
  ui.turn.textContent = current ? `${current.name}의 턴` : waiting ? "대기실" : "대국 종료";
  byId("turn-clock").hidden = !(current && state.mode === 1);
  ui.lobby.hidden = !waiting;
  ui.board.hidden = waiting;
  ui.settings.hidden = !(waiting && member === state.host);
  ui.close.hidden = ui.settings.hidden;
  ui.result.hidden = !state.result;
  byId("ready-controls").hidden = !(waiting && me);
  document.querySelector(".exit").hidden = Boolean(state.result);
  update("people", [state.players, state.spectators, state.turn, state.mode, state.phase], renderPeople);
  update("messages", state.messages, renderMessages);
  update("board", [state.board, state.turn, marker], renderBoard);
  if (waiting) update("lobby", [state.players, state.mode, state.seconds], () => renderLobby(me));
  if (state.result) update("result", state.result, renderResult);
  ui.connection.hidden = true;
  ui.room.hidden = false;
  placeChat();
}

function formatTime(ms, precise) {
  const units = Math.max(0, Math.ceil(ms / (precise ? 10 : 1000)));
  const seconds = precise ? Math.floor(units / 100) : units;
  const text = [Math.floor(seconds / 60), seconds % 60].map((value) => String(value).padStart(2, "0")).join(":");
  return precise ? `${text}.${String(units % 100).padStart(2, "0")}` : text;
}

function drawClocks(now) {
  if (state) {
    const remaining = (id) => (state.clocks[id] || 0) - (id === state.turn ? now - snapshotAt : 0);
    if (state.mode === 1 && state.turn) byId("turn-clock").textContent = formatTime(remaining(state.turn), false);
    for (const item of document.querySelectorAll("[data-clock]")) {
      item.textContent = formatTime(remaining(item.dataset.clock), true);
    }
  }
  requestAnimationFrame(drawClocks);
}

const mobile = matchMedia("(max-width: 760px), (pointer: coarse) and (max-width: 1100px)");
function placeChat() {
  if (!mobile.matches || ui.room.hidden) return;
  const viewport = window.visualViewport;
  const bottom = viewport ? viewport.offsetTop + viewport.height : innerHeight;
  const layoutHeight = ui.scroll.getBoundingClientRect().bottom;
  const scrollTop = ui.scroll.getBoundingClientRect().top;
  const peopleBottom = ui.people.getBoundingClientRect().bottom;
  const content = peopleBottom + ui.scroll.scrollTop - scrollTop;
  const maxTravel = layoutHeight * 0.4;
  ui.scroll.style.setProperty("--travel-pad", `${Math.max(0, ui.scroll.clientHeight + maxTravel - content)}px`);
  ui.chat.style.setProperty("--chat-bottom", `${Math.max(0, layoutHeight - bottom)}px`);
  ui.chat.style.setProperty("--chat-top", `${Math.max(scrollTop, Math.min(peopleBottom, bottom - 75))}px`);
}

ui.scroll.addEventListener("scroll", placeChat, {passive: true});
window.addEventListener("resize", placeChat);
window.visualViewport?.addEventListener("resize", placeChat);
window.visualViewport?.addEventListener("scroll", placeChat);

function leave() {
  if (!transmit("leave")) return;
  intentionalClose = true;
  finish("방에서 나갔습니다.");
}

function finish(text) {
  intentionalClose = true;
  ui.room.hidden = true;
  ui.connection.hidden = false;
  ui.connection.textContent = text;
}

function handle(data) {
  if (intentionalClose) return;
  if (data.type === "identity") member = data.member;
  else if (data.type === "room") applyRoom(data);
  else if (data.type === "closed") finish("방이 종료되었습니다.");
  else if (data.type === "error") notice(data.message);
  else if (data.type === "preview" && data.index === requested) {
    requested = undefined;
    if (data.status === "legal" || data.status === "win") marker = data.index;
    else if (data.status !== "occupied") notice("금수입니다");
    renderBoard();
  } else if (data.type === "move") {
    marker = undefined;
    if (data.status !== "legal" && data.status !== "win" && data.status !== "occupied") notice("금수입니다");
    renderBoard();
  }
}

function connect() {
  const endpoint = new URL(roomPath + "/events", location.href);
  endpoint.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(endpoint);
  socket.onmessage = ({data}) => handle(JSON.parse(data));
  socket.onclose = () => {
    if (intentionalClose) return;
    if (!member) return finish("입장권이 필요합니다. 디스코드에서 참가 또는 관전 버튼을 다시 눌러 주세요.");
    notice("연결이 끊겼습니다. 재연결 중입니다.");
    setTimeout(connect, 750);
  };
}

async function enter() {
  const ticket = new URLSearchParams(location.hash.slice(1)).get("ticket");
  if (ticket) {
    history.replaceState(null, "", roomPath);
    const response = await fetch(roomPath + "/admit", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ticket}),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      return finish(payload.error || "입장권이 만료되었거나 방이 종료되었습니다.");
    }
  }
  connect();
}

byId("normal-mode").addEventListener("click", () => {
  if (state?.mode !== 1) transmit("configure", {mode: 1, seconds: 30});
});
byId("blitz-mode").addEventListener("click", () => {
  if (state?.mode !== 2) transmit("configure", {mode: 2, seconds: 180});
});
ui.seconds.addEventListener("change", () => transmit("configure", {mode: state.mode, seconds: +ui.seconds.value}));
ui.black.addEventListener("click", () => transmit("color", {color: 1}));
ui.white.addEventListener("click", () => transmit("color", {color: 2}));
ui.ready.addEventListener("click", () => transmit("ready"));
ui.close.addEventListener("click", () => transmit("close"));
document.querySelector(".exit").addEventListener("click", leave);

let pendingCompositionSend = false;
function sendChat() {
  const text = ui.message.value;
  if (text.trim() && transmit("chat", {text})) ui.message.value = "";
}
ui.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendChat();
});
ui.message.addEventListener("keydown", (event) => {
  if ((event.key !== "Enter" && event.code !== "Enter" && event.code !== "NumpadEnter") || event.shiftKey) return;
  if (event.isComposing || event.keyCode === 229) {
    pendingCompositionSend = true;
    return;
  }
  event.preventDefault();
  if (!event.repeat) sendChat();
});
ui.message.addEventListener("compositionend", () => {
  if (!pendingCompositionSend) return;
  pendingCompositionSend = false;
  setTimeout(sendChat, 0);
});

ui.theme.addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme !== "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  ui.theme.setAttribute("aria-checked", String(dark));
});

requestAnimationFrame(drawClocks);
enter().catch(() => finish("방에 연결할 수 없습니다."));
