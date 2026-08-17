/* app.js — Alpine.js component definitions for Personal Aggregator */

/* ── Dwell-read timer ───────────────────────────────────────────────────────
   Accumulates visible-tab time while an unread article is open in the reader
   pane and fires an auto-read POST when the threshold is reached.
   Page Visibility API pauses accumulation while the tab is hidden so hidden
   time never counts toward the threshold.

   Public API: _dwell.arm(articleId, seconds) / _dwell.cancel()
   ─────────────────────────────────────────────────────────────────────────── */
const _dwell = (() => {
  let _timerId = null;
  let _articleId = null;
  let _targetMs = 0;
  let _accumulated = 0;    // ms of confirmed visible dwell
  let _visibleSince = null; // Date.now() timestamp when visible counting started

  function _schedule() {
    const remaining = Math.max(_targetMs - _accumulated, 0);
    _timerId = setTimeout(_onTimer, remaining + 50);
  }

  function _onTimer() {
    _timerId = null;
    if (!_articleId) return;
    // Tally any time elapsed since we last noted a visible-start
    if (!document.hidden && _visibleSince !== null) {
      const now = Date.now();
      _accumulated += now - _visibleSince;
      _visibleSince = now;
    }
    if (_accumulated >= _targetMs) {
      _fire();
    } else {
      _schedule();
    }
  }

  function _fire() {
    const id = _articleId;
    cancel();
    htmx.ajax('POST', `/article/${id}/read`, {
      target: '#article-detail',
      swap: 'outerHTML',
    });
  }

  function arm(articleId, seconds) {
    cancel();
    if (!articleId || seconds <= 0) return;
    _articleId = articleId;
    _targetMs = seconds * 1000;
    _accumulated = 0;
    _visibleSince = document.hidden ? null : Date.now();
    _schedule();
  }

  function cancel() {
    if (_timerId !== null) {
      clearTimeout(_timerId);
      _timerId = null;
    }
    _articleId = null;
    _accumulated = 0;
    _visibleSince = null;
  }

  // Pause accumulation when the tab is hidden; resume when it becomes visible.
  document.addEventListener('visibilitychange', () => {
    if (!_articleId) return;
    if (document.hidden) {
      if (_visibleSince !== null) {
        _accumulated += Date.now() - _visibleSince;
        _visibleSince = null;
      }
      if (_timerId !== null) {
        clearTimeout(_timerId);
        _timerId = null;
      }
    } else {
      _visibleSince = Date.now();
      _schedule();
    }
  });

  return { arm, cancel };
})();

/* When reader content is swapped, arm the dwell timer if an unread article
   detail is present (auto-read is off when WEB_AUTO_READ_SECONDS = 0).
   Fires for any content swap into #reader-content, including thread-member
   articles; naturally skips briefs because they carry no #article-detail. */
document.addEventListener('htmx:afterSwap', (event) => {
  if (event.detail.target.id !== 'reader-content') return;
  // Only (re)arm or cancel on GET loads; POST swaps (e.g. late-arriving auto-read
  // response) must not kill the timer already armed for the next article.
  if (event.detail.requestConfig && event.detail.requestConfig.verb !== 'get') return;
  const seconds = parseInt(document.body.dataset.autoReadSeconds || '0', 10);
  if (seconds <= 0) { _dwell.cancel(); return; }
  const detail = event.detail.target.querySelector('#article-detail');
  if (!detail) { _dwell.cancel(); return; }
  const id = detail.dataset.articleId;
  if (!id || detail.classList.contains('is-read')) { _dwell.cancel(); return; }
  _dwell.arm(parseInt(id, 10), seconds);
});

/* ── Root app component (bound to <body> in shell.html) ────────────────────
   Manages: sidebar drawer state, keyboard shortcuts help overlay (? key),
   reader close/open, and search focus.
   ────────────────────────────────────────────────────────────────────────── */
function aggregatorApp() {
  return {
    drawerOpen: false,
    showHelp: false,

    /* Handles keydown events on the window (delegated from @keydown.window).
       j/k/v/m/n are handled here (not in each list component) so they work on
       both feed and thread pages without depending on which centre-pane component
       is mounted. j/k dispatch reader:next/prev which each list component listens
       for; v/m/n act on the article currently loaded in #article-detail. */
    handleKey(event) {
      if (event.key === 'Escape') {
        if (this.showHelp) { this.showHelp = false; return; }
        if (document.body.classList.contains('reader-open')) { this.closeReader(); return; }
        return;
      }
      if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA') return;
      if (event.key === '?') {
        event.preventDefault();
        this.showHelp = !this.showHelp;
        return;
      }
      if (event.key === '/') {
        event.preventDefault();
        this.focusSearch();
        return;
      }
      if (this.showHelp) return;
      if (event.key === 'j') { event.preventDefault(); window.dispatchEvent(new CustomEvent('reader:next')); return; }
      if (event.key === 'k') { event.preventDefault(); window.dispatchEvent(new CustomEvent('reader:prev')); return; }
      if (event.key === 'v') { event.preventDefault(); this._openArticleSource(); return; }
      if (event.key === 'c') { event.preventDefault(); this._openArticleComments(); return; }
      if (event.key === 'm') { event.preventDefault(); this._toggleOpenArticleRead(); return; }
      if (event.key === 'n') { event.preventDefault(); this._markReadAndNext(); return; }
    },

    /* v — open the source URL of the article currently open in the reader pane. */
    _openArticleSource() {
      const detail = document.getElementById('article-detail');
      const url = detail?.dataset.sourceUrl;
      if (url) window.open(url, '_blank', 'noopener');
    },

    /* c — open the comments URL of the article currently open in the reader pane. */
    _openArticleComments() {
      const detail = document.getElementById('article-detail');
      const url = detail?.dataset.commentsUrl;
      if (url) window.open(url, '_blank', 'noopener');
    },

    /* m — toggle read/unread for the article currently open in the reader pane.
       Targets #article-detail so the route returns the detail as primary and the
       matching article card as OOB, keeping both representations in sync. */
    _toggleOpenArticleRead() {
      const detail = document.getElementById('article-detail');
      const id = detail?.dataset.articleId;
      if (!id) return;
      _dwell.cancel();
      const isRead = detail.classList.contains('is-read');
      const action = isRead ? 'unread' : 'read';
      htmx.ajax('POST', `/article/${id}/${action}`, {
        target: '#article-detail',
        swap: 'outerHTML',
      });
    },

    /* n — mark the open article read then advance the list by one item. */
    _markReadAndNext() {
      const detail = document.getElementById('article-detail');
      const id = detail?.dataset.articleId;
      if (id && !detail.classList.contains('is-read')) {
        _dwell.cancel();
        htmx.ajax('POST', `/article/${id}/read`, {
          target: '#article-detail',
          swap: 'outerHTML',
        });
      }
      window.dispatchEvent(new CustomEvent('reader:next'));
    },

    /* Remove reader-open from body and notify articleList to clear selection. */
    closeReader() {
      _dwell.cancel();
      document.body.classList.remove('reader-open');
      window.dispatchEvent(new CustomEvent('reader:closed'));
    },

    /* Toggle the sidebar drawer; close the reader first when opening so the
       sidebar (z-sidebar:20) is not hidden behind the reader overlay (z-reader:25). */
    toggleDrawer() {
      if (!this.drawerOpen) {
        this.closeReader();
      }
      this.drawerOpen = !this.drawerOpen;
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

    /* On init: apply remembered sort/hide-read prefs and register reader event listeners. */
    init() {
      if (this.baseUrl) {
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
      }

      /* Named handlers so destroy() can remove the exact same references. */
      this._onReaderNext = () => this.selectNext();
      this._onReaderPrev = () => this.selectPrev();
      this._onReaderReadNext = () => this.markReadAndNext();
      this._onReaderClosed = () => { this.selectedId = null; };
      window.addEventListener('reader:next', this._onReaderNext);
      window.addEventListener('reader:prev', this._onReaderPrev);
      window.addEventListener('reader:read-next', this._onReaderReadNext);
      window.addEventListener('reader:closed', this._onReaderClosed);
    },

    destroy() {
      window.removeEventListener('reader:next', this._onReaderNext);
      window.removeEventListener('reader:prev', this._onReaderPrev);
      window.removeEventListener('reader:read-next', this._onReaderReadNext);
      window.removeEventListener('reader:closed', this._onReaderClosed);
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
       and load it in the reader pane (mobile: slide-in overlay; desktop: 3rd pane). */
    select(id) {
      this.selectedId = id;
      const cards = this._cards();
      const idx = cards.findIndex(
        (c) => parseInt(c.dataset.articleId, 10) === id,
      );
      if (idx >= 0) {
        cards[idx].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
      this._loadReader(id);
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

    /* Load the given article into the reader content area via HTMX. */
    _loadReader(id) {
      const content = document.getElementById('reader-content');
      htmx.ajax('GET', `/article/${id}`, {
        target: '#reader-content',
        swap: 'innerHTML',
      });
      document.body.classList.add('reader-open');
      if (content) {
        content.addEventListener('htmx:afterSwap', () => { content.scrollTop = 0; }, { once: true });
      }
    },
  };
}

/* ── Brief list component (bound to the today list div in _today.html)
   Manages: brief card selection, reader pane loading, desktop auto-select.
   ─────────────────────────────────────────────────────────────────────── */

/* Persists the selected brief ID across HTMX-driven re-renders of #article-list.
   The generating banner polls every 5s and replaces #article-list innerHTML, which
   destroys and recreates the Alpine component. Without this variable, init() would
   always jump to the newest card, hiding what the user was reading. */
let _briefSelectedId = null;

function briefList() {
  return {
    selectedId: null,

    init() {
      if (window.innerWidth >= 1024) {
        if (_briefSelectedId !== null) {
          const existing = this.$el.querySelector(`[data-brief-id="${_briefSelectedId}"]`);
          if (existing) {
            this.selectedId = _briefSelectedId;
            return;
          }
        }
        const first = this.$el.querySelector('.brief-card');
        if (first) {
          this.selectBrief(parseInt(first.dataset.briefId, 10));
        }
      }
    },

    selectBrief(id) {
      this.selectedId = id;
      _briefSelectedId = id;
      const content = document.getElementById('reader-content');
      htmx.ajax('GET', `/brief/${id}`, {
        target: '#reader-content',
        swap: 'innerHTML',
      });
      document.body.classList.add('reader-open');
      if (content) {
        content.addEventListener('htmx:afterSwap', () => { content.scrollTop = 0; }, { once: true });
      }
    },
  };
}


/* ── Thread list component (bound to the thread list div in _thread_list.html)
   Manages: active thread selection (master-detail) and recluster button loading state.
   ──────────────────────────────────────────────────────────────────────────── */
function threadList() {
  return {
    selectedId: null,
    baseUrl: null,
    sortMode: 'importance',

    init() {
      if (this.baseUrl) {
        const persisted = localStorage.getItem('threadSort:' + this.baseUrl);
        if (persisted && persisted !== this.sortMode) {
          const url = this.baseUrl + (persisted === 'recent' ? '?sort=recent' : '');
          htmx.ajax('GET', url, { target: '#article-list', swap: 'innerHTML' });
        }
      }
      this._onNavNext = () => this.selectNext();
      this._onNavPrev = () => this.selectPrev();
      window.addEventListener('reader:next', this._onNavNext);
      window.addEventListener('reader:prev', this._onNavPrev);
    },

    destroy() {
      window.removeEventListener('reader:next', this._onNavNext);
      window.removeEventListener('reader:prev', this._onNavPrev);
    },

    /* Write sort preference to localStorage; called by sort toggle buttons. */
    setSortMode(mode) {
      if (this.baseUrl) {
        localStorage.setItem('threadSort:' + this.baseUrl, mode);
      }
    },

    /* Mark a thread as selected and open the reader pane. HTMX loads the detail
       via hx-get on the card's <a> tag; this just tracks selection state. */
    selectThread(id) {
      this.selectedId = id;
      document.body.classList.add('reader-open');
      const card = document.querySelector(`[data-thread-id="${id}"]`);
      card?.querySelector('.thread-update-dot')?.remove();
    },

    /* Return all thread card elements within this component's root element. */
    _cards() {
      return Array.from(this.$el.querySelectorAll('.thread-card'));
    },

    /* Find the index of the selected thread card. Returns -1 when none selected. */
    _currentIndex() {
      if (this.selectedId === null) return -1;
      return this._cards().findIndex(
        (c) => parseInt(c.dataset.threadId, 10) === this.selectedId,
      );
    },

    /* j — select next thread and load it into the reader pane. */
    selectNext() {
      const cards = this._cards();
      if (!cards.length) return;
      const next = Math.min(this._currentIndex() + 1, cards.length - 1);
      this._navigateToThread(parseInt(cards[next].dataset.threadId, 10));
    },

    /* k — select previous thread and load it into the reader pane. */
    selectPrev() {
      const cards = this._cards();
      if (!cards.length) return;
      const cur = this._currentIndex();
      const prev = cur <= 0 ? 0 : cur - 1;
      this._navigateToThread(parseInt(cards[prev].dataset.threadId, 10));
    },

    /* Select a thread, scroll its card into view, and load the detail into the reader. */
    _navigateToThread(id) {
      this.selectedId = id;
      document.body.classList.add('reader-open');
      const card = document.querySelector(`[data-thread-id="${id}"]`);
      if (card) {
        card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        card.querySelector('.thread-update-dot')?.remove();
      }
      const content = document.getElementById('reader-content');
      htmx.ajax('GET', `/threads/${id}`, { target: '#reader-content', swap: 'innerHTML' });
      if (content) {
        content.addEventListener('htmx:afterSwap', () => { content.scrollTop = 0; }, { once: true });
      }
    },
  };
}


/* Register component factories with Alpine so x-data="aggregatorApp" / "articleList"
   resolve correctly regardless of when exactly Alpine initialises relative to this
   script. This file must still be loaded BEFORE the Alpine CDN script so this
   listener is in place before Alpine fires 'alpine:init'. */
document.addEventListener('alpine:init', () => {
  /* Global store for sidebar collapse state. Lives outside the per-section x-data
     components so it survives HTMX innerHTML swaps of #sidebar. */
  window.Alpine.store('sidebar', {
    categoriesCollapsed: localStorage.getItem('sidebar.categories.collapsed') === 'true',
    sourcesCollapsed: localStorage.getItem('sidebar.sources.collapsed') === 'true',
    toggle(key) {
      this[key] = !this[key];
      const storageKey = key === 'categoriesCollapsed'
        ? 'sidebar.categories.collapsed'
        : 'sidebar.sources.collapsed';
      localStorage.setItem(storageKey, this[key]);
    },
  });

  window.Alpine.data('aggregatorApp', aggregatorApp);
  window.Alpine.data('articleList', articleList);
  window.Alpine.data('briefList', briefList);
  window.Alpine.data('threadList', threadList);
});
