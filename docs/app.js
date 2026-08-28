(function () {
  'use strict';

  var body = document.body;
  var puzzle = body.dataset.puzzle;
  var total = parseInt(body.dataset.total, 10) || 1;
  var STORE = 'komantle:open';
  var HOLD_MS = 600;

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function buzz(ms) {
    if (navigator.vibrate) { try { navigator.vibrate(ms); } catch (e) {} }
  }

  /* ------------------------------------------------ 열림 상태 (회차별) */

  function load() {
    try {
      var raw = JSON.parse(localStorage.getItem(STORE) || 'null');
      if (raw && raw.puzzle === puzzle && Array.isArray(raw.open)) return raw.open;
    } catch (e) {}
    return [];
  }

  var opened = load();

  function save() {
    try {
      localStorage.setItem(STORE, JSON.stringify({ puzzle: puzzle, open: opened }));
    } catch (e) {}
  }

  /* ------------------------------------------------ 진행 바 */

  var fill = document.getElementById('progress-fill');
  var count = document.getElementById('progress-count');

  function paint() {
    var n = opened.length;
    var ratio = Math.min(1, n / total);
    count.textContent = String(n);
    fill.style.setProperty('--p', (ratio * 100).toFixed(1) + '%');
    // 열수록 초록에서 빨강으로. 많이 열었다 = 스포일러를 많이 봤다.
    fill.style.setProperty('--pcolor', 'hsl(' + Math.round(145 - 145 * ratio) + ' 72% 45%)');
  }

  /* ------------------------------------------------ 힌트 카드 */

  function open(card, silent) {
    if (card.classList.contains('open')) return;
    card.classList.add('open');
    card.setAttribute('aria-expanded', 'true');
    var id = card.dataset.card || 'answer';
    if (opened.indexOf(id) === -1) { opened.push(id); save(); }
    paint();
    if (!silent) buzz(12);
  }

  var cards = document.querySelectorAll('.card[data-card]');
  Array.prototype.forEach.call(cards, function (card) {
    card.addEventListener('click', function () { open(card); });
    card.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(card); }
    });
  });

  /* ------------------------------------------------ 정답 — 길게 누르기 */

  var answerCard = document.getElementById('card-answer');

  function showAnswer(silent) {
    if (answerCard.classList.contains('open')) return;
    var bytes = Uint8Array.from(atob(answerCard.dataset.answer), function (c) {
      return c.charCodeAt(0);
    });
    document.getElementById('answer-text').textContent = new TextDecoder().decode(bytes);
    open(answerCard, silent);
    if (!silent) buzz(30);
  }

  var ring = answerCard.querySelector('.ring');
  var holdStart = 0;
  var raf = 0;
  var timer = 0;

  function setHold(v) { ring.style.setProperty('--hold', v.toFixed(3)); }

  // 링을 채우는 건 rAF, 실제로 여는 건 타이머다. 프레임이 throttle 돼도 열리긴 한다.
  function tick() {
    setHold(Math.min(1, (Date.now() - holdStart) / HOLD_MS));
    raf = requestAnimationFrame(tick);
  }

  function stopHold() {
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    if (timer) { clearTimeout(timer); timer = 0; }
    setHold(0);
  }

  answerCard.addEventListener('pointerdown', function (e) {
    if (answerCard.classList.contains('open')) return;
    e.preventDefault();
    holdStart = Date.now();
    raf = requestAnimationFrame(tick);
    timer = setTimeout(function () { stopHold(); showAnswer(false); }, HOLD_MS);
  });

  ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (evt) {
    answerCard.addEventListener(evt, stopHold);
  });

  // 마우스 드래그로 텍스트가 잡히는 것만 막는다. 터치 스크롤은 그대로 둔다.
  answerCard.addEventListener('contextmenu', function (e) { e.preventDefault(); });

  // 키보드는 길게 누를 수가 없다. Enter 한 번으로 연다.
  answerCard.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); showAnswer(false); }
  });

  /* ------------------------------------------------ 저장된 상태 복원 */

  opened.slice().forEach(function (id) {
    if (id === 'answer') { showAnswer(true); return; }
    var card = document.querySelector('.card[data-card="' + id + '"]');
    if (card) open(card, true);
    else opened.splice(opened.indexOf(id), 1);   // 오늘 비활성된 카드
  });
  save();
  paint();

  /* ------------------------------------------------ 스크롤 등장 */

  var reveals = document.querySelectorAll('.reveal');

  if (reduced || !('IntersectionObserver' in window)) {
    Array.prototype.forEach.call(reveals, function (el) { el.classList.add('shown'); });
    return;
  }

  var seen = 0;
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      entry.target.style.setProperty('--delay', Math.min(seen, 6) * 60 + 'ms');
      entry.target.classList.add('shown');
      seen += 1;
      io.unobserve(entry.target);
    });
    seen = 0;
  }, { rootMargin: '0px 0px -8% 0px', threshold: .06 });

  Array.prototype.forEach.call(reveals, function (el) { io.observe(el); });
})();
