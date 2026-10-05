// Keyboard triage: j/k move, y shortlist, n pass, o open listing, d open draft/detail.
(function () {
  let idx = 0;
  const cards = () => Array.from(document.querySelectorAll('#cards .card'));
  const focus = (i) => {
    const list = cards();
    if (!list.length) return;
    idx = Math.max(0, Math.min(i, list.length - 1));
    list.forEach((c, j) => c.classList.toggle('focused', j === idx));
    list[idx].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  };
  document.addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
    const list = cards();
    if (!list.length) return;
    const card = list[idx];
    switch (e.key) {
      case 'j': focus(idx + 1); break;
      case 'k': focus(idx - 1); break;
      case 'y': card.querySelector('button.up').click(); break;
      case 'n': card.querySelector('button.down').click(); break;
      case 'o': if (card.dataset.url) window.open(card.dataset.url, '_blank', 'noopener'); break;
      case 'd': window.location = '/l/' + card.dataset.id; break;
      default: return;
    }
    e.preventDefault();
  });
  // After a vote removes a card, keep focus at the same position.
  document.body.addEventListener('htmx:afterSwap', () => focus(idx));
  focus(0);
})();
