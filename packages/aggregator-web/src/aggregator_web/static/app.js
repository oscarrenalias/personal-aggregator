/* app.js — Alpine.js component definitions for Personal Aggregator */

/* ── Root app component (bound to <body> in shell.html) ────────────────────
   Manages: sidebar drawer state, keyboard shortcuts help overlay (? key),
   and search focus — triggered by the sidebar search button or the / key.
   The j/k/v/m shortcuts are handled by the nested articleList() component.
   ────────────────────────────────────────────────────────────────────────── */
function aggregatorApp() {
  return {
    drawerOpen: false,
    showHelp: false,

    /* Handles keydown events on the window (delegated from @keydown.window). */
    handleKey(event) {
      if (event.key === 'Escape') {
        if (this.showHelp) { this.showHelp = false; }
        return;
      }
      if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA') return;
      if (event.key === '?') {
        event.preventDefault();
        this.showHelp = !this.showHelp;
        return;
      }
      if (event.key !== '/') return;
      event.preventDefault();
      this.focusSearch();
    },

    /* Focus the sidebar search input. */
    focusSearch() {
      const el = document.getElementById('sidebar-search-input');
      if (el) {
        el.focus();
        el.select();
      }
    },
  };
}


/* ── Article list component (bound to the article list div in _article_list.html)
   Manages: keyboard selection, reader loading, read-toggle, external link open.
   ─────────────────────────────────────────────────────────────────────────────── */
function articleList() {
  return {
    /* ID of the currently selected article (null = no selection). */
    selectedId: null,

    /* Return all article card elements within this component's root element. */
    _cards() {
      return Array.from(this.$el.querySelectorAll('.article-card'));
    },

    /* Find the index of the selected card in the current DOM order.
       Returns -1 when nothing is selected or the selected card is gone. */
    _currentIndex() {
      if (this.selectedId === null) return -1;
      return this._cards().findIndex(
        (c) => parseInt(c.dataset.articleId, 10) === this.selectedId,
      );
    },

    /* Return the selected card element, or null. */
    _selectedCard() {
      if (this.selectedId === null) return null;
      return document.querySelector(`[data-article-id="${this.selectedId}"]`);
    },

    /* True when the active element is a text input — suppresses keyboard shortcuts. */
    _inputFocused() {
      const tag = document.activeElement && document.activeElement.tagName;
      return tag === 'INPUT' || tag === 'TEXTAREA';
    },

    /* Select the article with the given id: update state, scroll into view,
       and (on desktop) load it in the reader pane. */
    select(id) {
      this.selectedId = id;
      const cards = this._cards();
      const idx = cards.findIndex(
        (c) => parseInt(c.dataset.articleId, 10) === id,
      );
      if (idx >= 0) {
        cards[idx].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
      /* Desktop (three-pane layout ≥ 1024 px): load article in reader pane. */
      if (window.innerWidth >= 1024) {
        this._loadReader(id);
      }
    },

    /* j — select next article; clamped at the last card. */
    selectNext() {
      if (this._inputFocused()) return;
      const cards = this._cards();
      if (!cards.length) return;
      const next = Math.min(this._currentIndex() + 1, cards.length - 1);
      this.select(parseInt(cards[next].dataset.articleId, 10));
    },

    /* k — select previous article; clamped at the first card. */
    selectPrev() {
      if (this._inputFocused()) return;
      const cards = this._cards();
      if (!cards.length) return;
      const cur = this._currentIndex();
      const prev = cur <= 0 ? 0 : cur - 1;
      this.select(parseInt(cards[prev].dataset.articleId, 10));
    },

    /* v — open the selected article's source URL in a new tab. */
    openSelected() {
      if (this._inputFocused()) return;
      const card = this._selectedCard();
      if (!card) return;
      const url = card.dataset.sourceUrl;
      if (url) window.open(url, '_blank', 'noopener');
    },

    /* m — toggle read/unread for the selected article via HTMX POST.
       Reads the current read state from the .is-read class on the card. */
    toggleReadSelected() {
      if (this._inputFocused()) return;
      const card = this._selectedCard();
      if (!card || this.selectedId === null) return;
      const isRead = card.classList.contains('is-read');
      const action = isRead ? 'unread' : 'read';
      htmx.ajax('POST', `/article/${this.selectedId}/${action}`, {
        target: card,
        swap: 'outerHTML',
      });
    },

    /* n — mark the selected article read then advance to the next article.
       Captures the next card ID before the async HTMX swap alters the DOM. */
    markReadAndNext() {
      if (this._inputFocused()) return;
      const card = this._selectedCard();
      if (!card || this.selectedId === null) return;
      if (!card.classList.contains('is-read')) {
        htmx.ajax('POST', `/article/${this.selectedId}/read`, {
          target: card,
          swap: 'outerHTML',
        });
      }
      const cards = this._cards();
      const next = Math.min(this._currentIndex() + 1, cards.length - 1);
      if (next >= 0) {
        this.select(parseInt(cards[next].dataset.articleId, 10));
      }
    },

    /* Load the given article into the reader pane via HTMX. */
    _loadReader(id) {
      const pane = document.getElementById('reader-pane');
      htmx.ajax('GET', `/article/${id}`, {
        target: '#reader-pane',
        swap: 'innerHTML',
      });
      document.body.classList.add('reader-open');
      if (pane) {
        pane.addEventListener('htmx:afterSwap', () => { pane.scrollTop = 0; }, { once: true });
      }
    },
  };
}

/* ── Brief list component (bound to the today list div in _today.html)
   Manages: brief card selection, reader pane loading, desktop auto-select.
   ─────────────────────────────────────────────────────────────────────── */
function briefList() {
  return {
    selectedId: null,

    init() {
      if (window.innerWidth >= 1024) {
        const first = this.$el.querySelector('.brief-card');
        if (first) {
          this.selectBrief(parseInt(first.dataset.briefId, 10));
        }
      }
    },

    selectBrief(id) {
      this.selectedId = id;
      const pane = document.getElementById('reader-pane');
      htmx.ajax('GET', `/brief/${id}`, {
        target: '#reader-pane',
        swap: 'innerHTML',
      });
      document.body.classList.add('reader-open');
      if (pane) {
        pane.addEventListener('htmx:afterSwap', () => { pane.scrollTop = 0; }, { once: true });
      }
    },
  };
}


/* Register component factories with Alpine so x-data="aggregatorApp" / "articleList"
   resolve correctly regardless of when exactly Alpine initialises relative to this
   script. This file must still be loaded BEFORE the Alpine CDN script so this
   listener is in place before Alpine fires 'alpine:init'. */
document.addEventListener('alpine:init', () => {
  window.Alpine.data('aggregatorApp', aggregatorApp);
  window.Alpine.data('articleList', articleList);
  window.Alpine.data('briefList', briefList);
});
