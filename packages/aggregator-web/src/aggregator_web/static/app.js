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
   Manages: keyboard selection, reader loading, read-toggle, external link open,
   and per-feed sort preference (localStorage keyed by baseUrl).
   ─────────────────────────────────────────────────────────────────────────────── */
function articleList() {
  return {
    /* ID of the currently selected article (null = no selection). */
    selectedId: null,

    /* Set by the template via x-data spread; defaults keep the factory self-contained. */
    baseUrl: null,
    sortMode: 'relevance',
    unreadOnly: false,

    /* On init: apply remembered sort and hide-read preferences if they differ from the server. */
    init() {
      if (!this.baseUrl) return;
      const persistedSort = localStorage.getItem('feedSort:' + this.baseUrl);
      const persistedHideRead = localStorage.getItem('feedHideRead:' + this.baseUrl);
      const wantNewest = persistedSort === 'newest';
      const wantHideRead = persistedHideRead === 'hide';
      if ((wantNewest && this.sortMode !== 'newest') || (wantHideRead && !this.unreadOnly)) {
        const useNewest = wantNewest || this.sortMode === 'newest';
        const useHideRead = wantHideRead || this.unreadOnly;
        const params = [];
        if (useNewest) params.push('sort=newest');
        if (useHideRead) params.push('unread=1');
        const url = this.baseUrl + (params.length ? '?' + params.join('&') : '');
        htmx.ajax('GET', url, { target: '#article-list', swap: 'innerHTML' });
      }
    },

    /* Write sort preference to localStorage; called by sort toggle buttons. */
    setSortMode(mode) {
      if (this.baseUrl) {
        localStorage.setItem('feedSort:' + this.baseUrl, mode);
      }
    },

    /* Write hide-read preference to localStorage; called by hide-read toggle buttons. */
    setHideRead(hide) {
      if (this.baseUrl) {
        localStorage.setItem('feedHideRead:' + this.baseUrl, hide ? 'hide' : 'show');
      }
    },

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
