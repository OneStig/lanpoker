/* =========================================================
   LAN Poker - client
   Single render() pipeline driven by `state`. All socket
   events update state then re-render. No incremental DOM
   manipulation outside of render().
   ========================================================= */

(() => {
  // ---------- constants ----------
  const SUIT_CHAR  = { c: "♣", d: "♦", h: "♥", s: "♠" };
  const SUIT_COLOR = { c: "black", d: "red", h: "red", s: "black" };
  const STREET_LABEL = {
    preflop: "Preflop",
    flop: "Flop",
    turn: "Turn",
    river: "River",
    showdown: "Showdown",
    complete: "Complete",
  };

  // ---------- state ----------
  const state = {
    connected: false,
    username: null,
    myHole: [],
    public: null,        // server's public state snapshot
    legal: null,         // legal_actions for me, when it's my turn
    pendingApproval: false,
    sessionEnded: false,
    finalState: null,
    betAmount: null,     // current slider value (when sizing)
    logOpen: false,
  };

  // ---------- DOM ----------
  const $ = (id) => document.getElementById(id);
  const el = {
    joinOverlay: $("join-overlay"),
    joinForm:    $("join-form"),
    joinUser:    $("join-username"),
    joinStack:   $("join-stack"),
    joinMsg:     $("join-msg"),
    endedOverlay:$("ended-overlay"),
    endedFinal:  $("ended-final"),

    blinds:      $("blinds-display"),
    street:      $("street-display"),
    connDot:     $("conn-dot"),
    logToggle:   $("log-toggle"),
    logDrawer:   $("log-drawer"),
    logBody:     $("log-body"),
    logClose:    $("log-close"),

    opponents:   $("opponents"),
    pot:         $("pot-total"),
    board:       $("board"),
    sidePots:    $("side-pots"),
    message:     $("message"),

    mySeat:      $("my-seat"),
    myName:      $("my-name"),
    myRole:      $("my-role"),
    myStack:     $("my-stack"),
    myCards:     $("my-cards"),
    myActions:   $("my-actions"),
  };

  // ---------- socket ----------
  const socket = io({ transports: ["polling", "websocket"] });

  socket.on("connect", () => {
    state.connected = true;
    const saved = sessionStorage.getItem("poker-username");
    if (saved) {
      // Don't trust sessionStorage until the server confirms via hello_result.
      socket.emit("hello", { username: saved });
    }
    render();
  });

  socket.on("hello_result", (msg) => {
    if (msg.ok) {
      state.username = msg.username;
    } else {
      state.username = null;
      state.myHole = [];
      state.legal = null;
      state.pendingApproval = false;
      sessionStorage.removeItem("poker-username");
    }
    render();
  });

  socket.on("disconnect", () => {
    state.connected = false;
    render();
  });

  socket.on("join_result", (msg) => {
    if (msg.ok) {
      state.username = msg.username;
      state.pendingApproval = true;
      sessionStorage.setItem("poker-username", msg.username);
      el.joinMsg.classList.remove("error");
      el.joinMsg.textContent = msg.message || "";
      log(`Joined as ${msg.username}: ${msg.message}`);
    } else {
      el.joinMsg.classList.add("error");
      el.joinMsg.textContent = msg.message || "Join failed";
    }
    render();
  });

  socket.on("action_result", (msg) => {
    if (!msg.ok) log(`Action rejected: ${msg.message}`);
  });

  socket.on("legal_actions", (la) => {
    state.legal = la && Object.keys(la).length ? la : null;
    if (state.legal && state.betAmount == null) {
      state.betAmount = state.legal.min_raise_to || 0;
    }
    render();
  });

  socket.on("private", (msg) => {
    if (msg.type === "hole") {
      state.myHole = msg.cards || [];
      render();
    }
  });

  socket.on("table", (payload) => {
    if (payload.type === "state") {
      const prevHandActive = !!(state.public && state.public.hand);
      state.public = payload.state;
      if (payload.new_hand) {
        state.myHole = [];
        state.legal = null;
        state.betAmount = null;
        log("--- New hand ---");
      }
      // If you're seated, you're no longer pending.
      if (isSeated() || isSpectator()) state.pendingApproval = false;
      // When it becomes my turn, ask for legal actions.
      if (isMyTurn()) {
        socket.emit("legal_actions", { username: state.username });
      } else {
        state.legal = null;
        state.betAmount = null;
      }
      // Reset bet sizing whenever the public state advances.
      if (prevHandActive && !state.public.hand) state.legal = null;
      render();
    } else if (payload.type === "session_ended") {
      state.sessionEnded = true;
      state.finalState = payload.final;
      render();
    }
  });

  // ---------- helpers ----------
  function isSeated() {
    if (!state.public || !state.username) return false;
    return state.public.seats.some((s) => s.username === state.username);
  }
  function isSpectator() {
    if (!state.public || !state.username) return false;
    return state.public.spectators.includes(state.username);
  }
  function isInHand() {
    const h = state.public && state.public.hand;
    return !!(h && h.players.some((p) => p.username === state.username));
  }
  function isMyTurn() {
    const h = state.public && state.public.hand;
    return !!(h && h.to_act === state.username);
  }
  function myHandPlayer() {
    const h = state.public && state.public.hand;
    if (!h) return null;
    return h.players.find((p) => p.username === state.username) || null;
  }
  function mySeat() {
    if (!state.public) return null;
    return state.public.seats.find((s) => s.username === state.username) || null;
  }

  // ---------- card rendering ----------
  function cardFace(card, size = "md") {
    if (!card) return cardEmpty(size);
    const rank = card[0] === "T" ? "10" : card[0];
    const suit = card[1];
    const ch = SUIT_CHAR[suit] || "?";
    const color = SUIT_COLOR[suit] || "black";
    return `
      <div class="card card--face card--${size}" data-color="${color}">
        <span class="card-rank">${rank}</span><span class="card-suit">${ch}</span>
      </div>`;
  }
  function cardBack(size = "sm") {
    return `<div class="card card--back card--${size}"></div>`;
  }
  function cardEmpty(size = "md") {
    return `<div class="card card--empty card--${size}"></div>`;
  }

  // ---------- render ----------
  function render() {
    renderTopBar();
    renderJoinOverlay();
    renderEndedOverlay();
    renderOpponents();
    renderCenter();
    renderMySeat();
    renderLogDrawer();
  }

  function renderTopBar() {
    el.connDot.dataset.connected = state.connected ? "true" : "false";
    el.connDot.title = state.connected ? "Connected" : "Disconnected";

    const p = state.public;
    if (p) {
      el.blinds.textContent = `${p.small_blind}/${p.big_blind}`;
      el.street.textContent = p.hand ? STREET_LABEL[p.hand.street] || p.hand.street : "—";
    } else {
      el.blinds.textContent = "—";
      el.street.textContent = "—";
    }
  }

  function renderJoinOverlay() {
    const showJoin = !state.username && !state.sessionEnded;
    el.joinOverlay.hidden = !showJoin;
  }

  function renderEndedOverlay() {
    el.endedOverlay.hidden = !state.sessionEnded;
    if (!state.sessionEnded || !state.finalState) return;
    const rows = [];
    for (const s of state.finalState.seated) {
      rows.push(`<div class="row"><span>${esc(s.username)}</span><span>${s.stack}</span></div>`);
    }
    for (const u of state.finalState.spectators) {
      rows.push(`<div class="row"><span>${esc(u)} (spectator)</span><span>—</span></div>`);
    }
    el.endedFinal.innerHTML = rows.join("") || `<div class="row"><span>(empty)</span></div>`;
  }

  function renderOpponents() {
    const p = state.public;
    if (!p) { el.opponents.innerHTML = ""; return; }

    // Pick the source: hand players if a hand is in progress, else seats.
    // Always exclude me.
    const hand = p.hand;
    const me = state.username;

    let entries;
    if (hand) {
      const buttonName = seatToUsername(hand.button_seat);
      entries = hand.players
        .filter((pl) => pl.username !== me)
        .map((pl) => ({
          username: pl.username,
          stack: pl.stack,
          bet: pl.committed_street,
          folded: pl.folded,
          allIn: pl.all_in,
          isButton: pl.username === buttonName,
          isToAct: hand.to_act === pl.username,
          inHand: true,
        }));
    } else {
      entries = p.seats
        .filter((s) => s.username !== me)
        .map((s) => ({
          username: s.username,
          stack: s.stack,
          bet: 0,
          folded: false,
          allIn: false,
          isButton: false,
          isToAct: false,
          inHand: false,
        }));
    }

    el.opponents.innerHTML = entries.map(opponentChip).join("");
  }

  function seatToUsername(seatNum) {
    if (!state.public || seatNum == null) return null;
    const s = state.public.seats.find((s) => s.seat === seatNum);
    return s ? s.username : null;
  }

  function opponentChip(p) {
    const cards = p.inHand
      ? (p.folded ? `<div class="card card--xs card--empty"></div><div class="card card--xs card--empty"></div>`
                  : cardBack("xs") + cardBack("xs"))
      : `<div class="card card--xs card--empty"></div><div class="card card--xs card--empty"></div>`;

    let status = "";
    if (p.folded) status = "Folded";
    else if (p.allIn) status = "All in";
    else if (p.isToAct) status = "To act";
    else if (!p.inHand) status = "Sitting out";

    return `
      <div class="opp" data-folded="${p.folded}" data-toact="${p.isToAct}">
        <div class="opp-name">
          ${p.isButton ? `<span class="btn-tag" title="Button">D</span>` : ""}
          <span>${esc(p.username)}</span>
        </div>
        <div class="opp-cards">${cards}</div>
        <div class="opp-stack">${formatChips(p.stack)}</div>
        <div class="opp-bet">
          <span class="opp-status">${status}</span>
          <span class="opp-bet-amount">${p.bet > 0 ? formatChips(p.bet) : ""}</span>
        </div>
      </div>`;
  }

  function renderCenter() {
    const p = state.public;
    const hand = p && p.hand;

    if (!p) {
      el.pot.textContent = "0";
      el.board.innerHTML = "";
      el.sidePots.innerHTML = "";
      el.message.textContent = "Connecting...";
      return;
    }

    if (!hand) {
      el.pot.textContent = "0";
      el.board.innerHTML = Array.from({ length: 5 }, () => cardEmpty("md")).join("");
      el.sidePots.innerHTML = "";
      const seated = p.seats.length;
      el.message.classList.remove("win");
      if (seated < 2) el.message.textContent = "Waiting for players...";
      else el.message.textContent = "Waiting for host to deal";
      return;
    }

    el.pot.textContent = formatChips(hand.pot_total);

    // Board: pad to 5 slots so layout is stable.
    const board = hand.board.slice();
    while (board.length < 5) board.push(null);
    el.board.innerHTML = board.map((c) => c ? cardFace(c, "md") : cardEmpty("md")).join("");

    // Side pots
    if (hand.pots && hand.pots.length > 1) {
      el.sidePots.innerHTML = hand.pots.map((pot, i) =>
        `<span class="side-pot">${i === 0 ? "Main" : "Side " + i}: <b>${formatChips(pot.amount)}</b></span>`
      ).join("");
    } else {
      el.sidePots.innerHTML = "";
    }

    // Message: winners / current actor
    if (hand.winners && hand.winners.length) {
      const lines = hand.winners.map((w) => `${w.usernames.join(", ")} wins ${formatChips(w.amount)}`);
      el.message.classList.add("win");
      el.message.textContent = lines.join("  ·  ");
    } else if (hand.to_act) {
      el.message.classList.remove("win");
      el.message.textContent = hand.to_act === state.username
        ? "Your turn"
        : `${hand.to_act} to act`;
    } else {
      el.message.classList.remove("win");
      el.message.textContent = "";
    }
  }

  function renderMySeat() {
    const p = state.public;

    // Name + role
    el.myName.textContent = state.username || "Guest";
    el.mySeat.dataset.toact = isMyTurn() ? "true" : "false";

    let role = "";
    if (state.pendingApproval) role = "Awaiting host";
    else if (isSeated()) role = isInHand() ? "Seated" : "Seated · sitting out";
    else if (isSpectator()) role = "Spectator";
    el.myRole.textContent = role;

    // Stack
    const seat = mySeat();
    const handMe = myHandPlayer();
    const stack = handMe ? handMe.stack : (seat ? seat.stack : null);
    el.myStack.textContent = stack != null ? formatChips(stack) : "";

    // Cards
    el.myCards.innerHTML = (handMe && state.myHole.length)
      ? state.myHole.map((c) => cardFace(c, "lg")).join("")
      : ((isSeated() && p && p.hand) ? cardEmpty("lg") + cardEmpty("lg") : "");

    // Actions / message
    el.myActions.innerHTML = "";
    if (state.pendingApproval) {
      el.myActions.appendChild(messageBlock("Waiting for the host to approve your join request."));
    } else if (isSpectator()) {
      const block = messageBlock("You're spectating.");
      const btn = document.createElement("button");
      btn.className = "btn btn-primary";
      btn.textContent = "Request a seat";
      btn.onclick = () => socket.emit("request_seat", { username: state.username });
      block.appendChild(btn);
      el.myActions.appendChild(block);
    } else if (isSeated() && (!p || !p.hand)) {
      el.myActions.appendChild(messageBlock("The next hand will start when the host deals."));
    } else if (isSeated() && !isInHand()) {
      el.myActions.appendChild(messageBlock("You'll be in the next hand."));
    } else if (isMyTurn() && state.legal) {
      renderActionPanel();
    } else if (isInHand()) {
      const handMe = myHandPlayer();
      if (handMe && handMe.folded) el.myActions.appendChild(messageBlock("You folded this hand."));
      else if (handMe && handMe.all_in) el.myActions.appendChild(messageBlock("You're all in."));
      else el.myActions.appendChild(messageBlock("Waiting for other players..."));
    } else if (!state.username) {
      // Join overlay handles this
    }
  }

  function messageBlock(text) {
    const div = document.createElement("div");
    div.className = "my-message";
    div.textContent = text;
    return div;
  }

  function renderActionPanel() {
    const la = state.legal;
    const root = el.myActions;

    // --- top row: fold / check-or-call / bet-or-raise ---
    const row = document.createElement("div");
    row.className = "action-row";

    const foldBtn = mkBtn("Fold", "btn btn-fold", () => act("fold"));
    foldBtn.disabled = false; // fold always legal on your turn
    row.appendChild(foldBtn);

    let midBtn;
    if (la.can_check) {
      midBtn = mkBtn("Check", "btn btn-check", () => act("check"));
    } else if (la.can_call) {
      midBtn = mkBtn(`Call ${formatChips(la.call_amount)}`, "btn btn-check", () => act("call"));
    } else {
      midBtn = mkBtn("Call", "btn btn-check", () => {});
      midBtn.disabled = true;
    }
    row.appendChild(midBtn);

    const canSize = la.can_bet || la.can_raise;
    const sizeLabel = la.can_bet ? "Bet" : "Raise";
    const sizeBtn = mkBtn(sizeLabel, "btn btn-bet", () => {
      const amt = clamp(state.betAmount, la.min_raise_to, la.max_raise_to);
      act(la.can_bet ? "bet" : "raise", amt);
    });
    sizeBtn.disabled = !canSize;
    row.appendChild(sizeBtn);

    root.appendChild(row);

    // --- bet sizing panel ---
    if (canSize) {
      if (state.betAmount == null) state.betAmount = la.min_raise_to;
      const amt = clamp(state.betAmount, la.min_raise_to, la.max_raise_to);
      state.betAmount = amt;
      sizeBtn.textContent = `${sizeLabel} ${formatChips(amt)}`;

      const panel = document.createElement("div");
      panel.className = "bet-panel";

      const head = document.createElement("div");
      head.className = "bet-head";
      head.innerHTML = `<span>${sizeLabel} to</span><b>${formatChips(amt)}</b>`;
      panel.appendChild(head);

      const slider = document.createElement("input");
      slider.type = "range";
      slider.className = "bet-slider";
      slider.min = la.min_raise_to;
      slider.max = la.max_raise_to;
      slider.step = 1;
      slider.value = amt;
      slider.style.setProperty("--fill", sliderFill(amt, la.min_raise_to, la.max_raise_to) + "%");
      slider.addEventListener("input", (e) => {
        const v = clamp(parseInt(e.target.value, 10) || la.min_raise_to, la.min_raise_to, la.max_raise_to);
        state.betAmount = v;
        head.querySelector("b").textContent = formatChips(v);
        sizeBtn.textContent = `${sizeLabel} ${formatChips(v)}`;
        slider.style.setProperty("--fill", sliderFill(v, la.min_raise_to, la.max_raise_to) + "%");
      });
      panel.appendChild(slider);

      const quicks = document.createElement("div");
      quicks.className = "bet-quicks";
      const presets = computePresets(la);
      for (const [label, value] of presets) {
        const c = document.createElement("button");
        c.type = "button";
        c.className = "chip";
        c.textContent = label;
        c.onclick = () => {
          slider.value = value;
          slider.dispatchEvent(new Event("input", { bubbles: true }));
        };
        quicks.appendChild(c);
      }
      panel.appendChild(quicks);

      root.appendChild(panel);
    }
  }

  function computePresets(la) {
    const pot = (state.public && state.public.hand) ? state.public.hand.pot_total : 0;
    const cur = (state.public && state.public.hand) ? state.public.hand.current_bet : 0;
    const presets = [
      ["Min", la.min_raise_to],
      ["½ Pot", Math.round(cur + pot * 0.5)],
      ["Pot", Math.round(cur + pot)],
      ["All in", la.max_raise_to],
    ];
    return presets.map(([k, v]) => [k, clamp(v, la.min_raise_to, la.max_raise_to)]);
  }

  function sliderFill(v, lo, hi) {
    if (hi <= lo) return 0;
    return ((v - lo) / (hi - lo)) * 100;
  }

  function mkBtn(text, cls, onclick) {
    const b = document.createElement("button");
    b.className = cls;
    b.type = "button";
    b.textContent = text;
    b.onclick = onclick;
    return b;
  }

  function act(action, amount = 0) {
    socket.emit("action", { username: state.username, action, amount });
  }

  // ---------- log drawer ----------
  function log(msg) {
    const ts = new Date().toLocaleTimeString();
    const line = document.createElement("div");
    line.className = "log-line";
    line.innerHTML = `<span class="ts">${ts}</span>${esc(msg)}`;
    el.logBody.appendChild(line);
    el.logBody.scrollTop = el.logBody.scrollHeight;
  }

  function renderLogDrawer() {
    el.logDrawer.hidden = !state.logOpen;
  }

  el.logToggle.addEventListener("click", () => {
    state.logOpen = !state.logOpen;
    renderLogDrawer();
  });
  el.logClose.addEventListener("click", () => {
    state.logOpen = false;
    renderLogDrawer();
  });

  // ---------- join form ----------
  el.joinForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const u = el.joinUser.value.trim();
    const st = parseInt(el.joinStack.value, 10);
    if (!u) {
      el.joinMsg.classList.add("error");
      el.joinMsg.textContent = "Enter a display name";
      return;
    }
    if (!st || st <= 0) {
      el.joinMsg.classList.add("error");
      el.joinMsg.textContent = "Buy-in must be positive";
      return;
    }
    el.joinMsg.classList.remove("error");
    el.joinMsg.textContent = "Sending request...";
    socket.emit("join", { username: u, stack: st });
  });

  // ---------- utils ----------
  function clamp(v, lo, hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
  }
  function formatChips(n) {
    if (n == null) return "—";
    return Number(n).toLocaleString();
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ---------- initial paint ----------
  render();
})();
