/**
 * ForceGraph — Investigation-style expanding graph
 * Part of DotDNA Charts Library
 *
 * Click any node to expand its children in place. All nodes persist.
 * The graph grows organically. 3 configurable levels, last links to Explorer.
 * Smooth JS-driven animations (requestAnimationFrame) for all transitions.
 */

class DotDNAForceGraph extends DotDNASVGChartBase {

    static CATEGORY_COLORS = {
        server:        { light: '#3b82f6', dark: '#60a5fa' },
        workstation:   { light: '#8b5cf6', dark: '#a78bfa' },
        network:       { light: '#0891b2', dark: '#22d3ee' },
        mobile:        { light: '#ea580c', dark: '#fb923c' },
        ot:            { light: '#d97706', dark: '#fbbf24' },
        iot:           { light: '#059669', dark: '#34d399' },
        miscellaneous: { light: '#64748b', dark: '#94a3b8' }
    };

    static CATEGORY_ICONS = {
        server: '<rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
        workstation: '<rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
        network: '<rect x="2" y="6" width="20" height="12" rx="2" ry="2"/><line x1="2" y1="12" x2="22" y2="12"/><rect x="5" y="14" width="2" height="2" rx="0.5"/><rect x="9" y="14" width="2" height="2" rx="0.5"/><rect x="13" y="14" width="2" height="2" rx="0.5"/><rect x="17" y="14" width="2" height="2" rx="0.5"/>',
        mobile: '<rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/>',
        ot: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
        iot: '<path d="M12 2a7 7 0 0 1 7 7c0 2.38-1.19 4.47-3 5.74V17a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 0 1 7-7z"/><path d="M9 21h6"/>',
        miscellaneous: '<circle cx="12" cy="12" r="10"/><circle cx="8" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="16" cy="12" r="1"/>'
    };

    constructor(containerId, config = {}) {
        super(containerId, config);
        this.config = {
            level1: config.level1 || config.dimension || 'category',
            level2: config.level2 || 'os_normalized',
            level3: config.level3 || 'data_source',
            filters: config.filters || [],
            maxChildren: config.maxChildren || 10,
            gridSize: config.gridSize || '4x4',
            expandedPaths: config.expandedPaths || []
        };

        // Graph data
        this.nodes = [];       // [{id, label, value, pct, color, dim, depth, pid, expanded, x, y, r, _rx, _ry, _op, _targetOp}]
        this.edges = [];       // [{src, tgt, key}]
        this._nmap = new Map();  // id → node
        this._nels = new Map();  // id → <g> element
        this._eels = new Map();  // key → <path> element
        this._nextId = 0;
        this.data = null;
        this.animating = false;
        this._raf = null;

        // ViewBox state (smooth interpolation) — landscape 3:2 ratio
        this._vb = { x: 0, y: 0, w: 1400, h: 700 };
        this._vbT = { x: 0, y: 0, w: 1400, h: 700 };

        // SVG layer groups (persistent)
        this._edgeLayer = null;
        this._nodeLayer = null;

        this.setupDOM();
        if (config.data) this.setData(config.data);
    }

    // ═══════════ DOM ═══════════

    setupDOM() {
        this.container.innerHTML = '';
        this.container.className = 'dotdna-chart';
        Object.assign(this.container.style, {
            width: '100%', height: '100%', display: 'flex',
            flexDirection: 'column', position: 'relative', overflow: 'hidden'
        });

        const wrap = document.createElement('div');
        Object.assign(wrap.style, { flex: '1', overflow: 'hidden', position: 'relative' });
        this.container.appendChild(wrap);

        // Breadcrumb at bottom
        this._bcBar = document.createElement('div');
        Object.assign(this._bcBar.style, {
            minHeight: '22px', fontSize: '11px', padding: '6px 10px 10px',
            display: 'flex', alignItems: 'center', gap: '6px', flexShrink: '0'
        });
        this.container.appendChild(this._bcBar);

        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.setAttribute('viewBox', `${this._vb.x} ${this._vb.y} ${this._vb.w} ${this._vb.h}`);
        this.svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
        Object.assign(this.svg.style, { width: '100%', height: '100%' });
        wrap.appendChild(this.svg);

        // Persistent layers
        this._edgeLayer = this._el('g', { class: 'fg-edges' });
        this._nodeLayer = this._el('g', { class: 'fg-nodes' });
        this.svg.appendChild(this._edgeLayer);
        this.svg.appendChild(this._nodeLayer);

        this.createTooltip();
    }

    // ═══════════ Public API ═══════════

    setData(data) {
        if (!data || !Array.isArray(data)) return;
        this.data = data;
        this._clearGraph();

        const sorted = [...data].sort((a, b) => b.value - a.value).slice(0, this.config.maxChildren);
        if (!sorted.length) return;

        // Level-0: circle layout, centered in the canvas
        const cx = 700, cy = 350;
        const sizes = this._sizes(sorted, 32, 58);
        const circleRadius = Math.max(150, sorted.length * 28);

        sorted.forEach((d, i) => {
            const a = (2 * Math.PI * i) / sorted.length - Math.PI / 2;
            const n = this._addNode({
                label: d.label, value: d.value, pct: d.percentage,
                color: d.color || this._color(d.label, this.config.level1),
                dim: this.config.level1, depth: 0, pid: null,
                x: cx + circleRadius * Math.cos(a),
                y: cy + circleRadius * Math.sin(a),
                r: sizes[i]
            });
            // Start at center for initial animation
            n._rx = cx; n._ry = cy; n._op = 0; n._targetOp = 1;
        });

        this._simulate();
        this._createAllElements();
        this._fitVB();
        this._animate(700, () => {
            this._breadcrumb();
            // Replay saved expansion state
            if (this.config.expandedPaths && this.config.expandedPaths.length > 0) {
                this._replayExpansions([...this.config.expandedPaths]);
            }
        });
    }

    // Returns current expansion state for saving
    getState() {
        const paths = [];
        // Walk expanded nodes in depth order
        const expanded = this.nodes.filter(n => n.expanded).sort((a, b) => a.depth - b.depth);
        for (const n of expanded) {
            paths.push({ dim: n.dim, label: n.label, depth: n.depth });
        }
        return { expandedPaths: paths };
    }

    // Replay a saved expansion path sequentially
    _replayExpansions(paths) {
        if (!paths.length || this.animating) return;
        const next = paths.shift();
        // Find the matching node at the right depth
        const node = this.nodes.find(n =>
            n.dim === next.dim && n.label === next.label && n.depth === next.depth && !n.expanded
        );
        if (!node) return;

        // Expand it, then continue with the rest after animation completes
        const origExpand = this._expand.bind(this);
        const origAnimate = this._animate.bind(this);

        // Temporarily patch _animate to chain the next replay after it completes
        const self = this;
        const realAnimate = this._animate;
        this._animate = function(dur, onDone) {
            realAnimate.call(self, dur, () => {
                if (onDone) onDone();
                self._animate = realAnimate; // restore
                self._replayExpansions(paths); // continue chain
            });
        };
        this._handleClick(node);
    }

    resize(gridSize) {
        this.config.gridSize = gridSize;
        this.setupDOM();
        if (this.data) this.setData(this.data);
    }

    // ═══════════ Theme ═══════════

    _dark() { return document.body.classList.contains('dark-mode'); }

    _color(label, dim) {
        if (dim === 'category') {
            const e = DotDNAForceGraph.CATEGORY_COLORS[(label || '').toLowerCase()];
            if (e) return this._dark() ? e.dark : e.light;
        }
        if (dim === 'data_source' && window.DataSourceLogos) {
            const l = window.DataSourceLogos.getDataSourceLogo(label);
            if (l && l.color) return l.color;
        }
        return this._dark() ? '#60a5fa' : '#3b82f6';
    }

    _t() {
        return this._dark()
            ? { text: '#e2e8f0', sec: '#94a3b8', line: 'rgba(148,163,184,0.18)', ring: 'rgba(148,163,184,0.3)' }
            : { text: '#1e293b', sec: '#64748b', line: 'rgba(148,163,184,0.22)', ring: 'rgba(148,163,184,0.4)' };
    }

    // ═══════════ Graph Data ═══════════

    _addNode(props) {
        const n = {
            id: this._nextId++, expanded: false,
            _rx: props.x, _ry: props.y, _op: 1, _targetOp: 1,
            ...props
        };
        this.nodes.push(n);
        this._nmap.set(n.id, n);
        return n;
    }

    _addEdge(src, tgt) {
        const key = `${src}-${tgt}`;
        this.edges.push({ src, tgt, key });
        return key;
    }

    _clearGraph() {
        if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
        this.nodes = []; this.edges = [];
        this._nmap.clear(); this._nels.clear(); this._eels.clear();
        this._nextId = 0; this.animating = false;
        this._edgeLayer.innerHTML = '';
        this._nodeLayer.innerHTML = '';
    }

    _dimForDepth(depth) {
        return [this.config.level1, this.config.level2, this.config.level3][depth] || this.config.level1;
    }

    // ═══════════ Force Simulation ═══════════

    _simulate() {
        const N = this.nodes;
        if (N.length < 2) return;

        const hasExpanded = N.some(n => n.expanded);
        // Spread columns wider: L0 left, L1 middle, L2 right
        const depthX = hasExpanded ? [250, 700, 1150] : null;
        // Scale repulsion with node count — very aggressive to prevent crowding
        const repForce = Math.max(3500, N.length * 300);

        for (let iter = 0; iter < 120; iter++) {
            // Repulsion — stronger to keep nodes well separated
            for (let i = 0; i < N.length; i++) {
                for (let j = i + 1; j < N.length; j++) {
                    const dx = N[i].x - N[j].x, dy = N[i].y - N[j].y;
                    const d2 = dx * dx + dy * dy;
                    const d = Math.sqrt(d2) || 1;
                    // Use pill width (1.8 * r) for horizontal clearance
                    const min = N[i].r * 1.8 + N[j].r * 1.8 + 45;
                    if (d < min * 5) {
                        const f = repForce / d2;
                        N[i].x += (dx / d) * f; N[i].y += (dy / d) * f;
                        N[j].x -= (dx / d) * f; N[j].y -= (dy / d) * f;
                    }
                }
            }

            // Edge attraction — keep parent-child connected but with room
            for (const e of this.edges) {
                const s = this._nmap.get(e.src), t = this._nmap.get(e.tgt);
                if (!s || !t) continue;
                const dx = t.x - s.x, dy = t.y - s.y;
                const d = Math.sqrt(dx * dx + dy * dy) || 1;
                const ideal = s.r + t.r + 90;
                const pull = (d - ideal) * 0.005;
                s.x += (dx / d) * pull; s.y += (dy / d) * pull;
                t.x -= (dx / d) * pull; t.y -= (dy / d) * pull;
            }

            if (depthX) {
                // Horizontal hierarchy: pull nodes toward their depth column
                for (const n of N) {
                    const tx = depthX[Math.min(n.depth, 2)];
                    n.x += (tx - n.x) * 0.03;
                }
            } else {
                // No expansion: gentle pull toward center
                for (const n of N) {
                    n.x += (700 - n.x) * 0.005;
                }
            }

            // Vertical centering — pull toward middle of canvas
            for (const n of N) {
                n.y += (350 - n.y) * 0.004;
            }
        }
    }

    // ═══════════ SVG Element Creation ═══════════

    _createAllElements() {
        // Edges
        for (const e of this.edges) {
            if (!this._eels.has(e.key)) this._createEdgeEl(e);
        }
        // Nodes
        for (const n of this.nodes) {
            if (!this._nels.has(n.id)) this._createNodeEl(n);
        }
    }

    _createEdgeEl(edge) {
        const p = this._el('path', {
            fill: 'none', stroke: this._t().line, 'stroke-width': 1.5, class: 'fg-edge'
        });
        p.style.opacity = '0';
        this._edgeLayer.appendChild(p);
        this._eels.set(edge.key, p);
    }

    _createNodeEl(node) {
        const t = this._t();
        const g = this._el('g', { class: 'fg-node', 'data-id': node.id });
        g.setAttribute('transform', `translate(${node._rx},${node._ry})`);
        g.style.opacity = String(node._op);
        g.style.cursor = 'pointer';

        // Glow
        const glowId = `fg-gl-${this.container.id}-${node.id}`;
        let defs = this.svg.querySelector('defs');
        if (!defs) { defs = this._el('defs'); this.svg.prepend(defs); }
        // No glow — clean circles only

        // Tic-tac / pill shape — wider than tall for better label fit
        const pw = node.r * 1.8;  // pill half-width
        const ph = node.r * 0.75; // pill half-height
        const pr = ph;             // corner radius = half-height for full rounding
        const pillBg = this._dark() ? '#303034' : '#f5f7fa';

        // Solid fill so badge can intersect the border
        g.appendChild(this._el('rect', {
            x: -pw, y: -ph, width: pw * 2, height: ph * 2,
            rx: pr, ry: pr, fill: pillBg,
            stroke: node.color, 'stroke-width': 1.2, 'stroke-opacity': 0.5, class: 'fg-ring'
        }));

        // Text label inside pill
        this._addLabel(g, node, pw);

        // Badge — border passes through the middle of the circle
        const bx = pw - 6, by = -ph;
        const br = node.value >= 1000 ? 13 : 11;
        g.appendChild(this._el('circle', { cx: bx, cy: by, r: br, fill: node.color }));
        const bt = this._el('text', {
            x: bx, y: by, 'text-anchor': 'middle', 'dominant-baseline': 'central',
            fill: '#fff', 'font-size': node.value >= 1000 ? 7.5 : 8.5, 'font-weight': 700
        });
        bt.textContent = node.value >= 10000 ? Math.round(node.value / 1000) + 'k' : node.value.toLocaleString();
        g.appendChild(bt);

        // Level 3 link indicator
        if (node.depth === 2) {
            const li = this._el('text', { x: node.r - 2, y: -node.r + 5, fill: node.color, 'font-size': 10, 'text-anchor': 'middle' });
            li.textContent = '\u2197';
            g.appendChild(li);
        }

        // Events
        g.addEventListener('mouseenter', e => {
            if (this.animating) return;
            g.setAttribute('transform', `translate(${node._rx},${node._ry}) scale(1.08)`);
            this.showTooltip(`${node.label}: ${this._fmtN(node.value)}${node.pct != null ? ' (' + node.pct.toFixed(1) + '%)' : ''}`, e);
        });
        g.addEventListener('mousemove', e => this.moveTooltip(e));
        g.addEventListener('mouseleave', () => {
            g.setAttribute('transform', `translate(${node._rx},${node._ry})`);
            this.hideTooltip();
        });
        g.addEventListener('click', () => {
            if (this.animating) return;
            this._handleClick(node);
        });

        this._nodeLayer.appendChild(g);
        this._nels.set(node.id, g);
    }

    _addLabel(g, node, pillWidth) {
        // Fit text inside the pill — much more horizontal room
        const availWidth = (pillWidth || node.r * 1.8) * 2 - 12;
        const fontSize = Math.max(8, Math.min(13, node.r * 0.42));
        const charWidth = fontSize * 0.58;
        const maxChars = Math.max(3, Math.floor(availWidth / charWidth));
        const label = node.label || '';
        const display = label.length > maxChars ? label.slice(0, maxChars - 1) + '\u2026' : label;

        const txt = this._el('text', {
            x: 0, y: 0, 'text-anchor': 'middle', 'dominant-baseline': 'central',
            fill: node.color, 'font-size': fontSize, 'font-weight': 600,
            'letter-spacing': '0.3'
        });
        txt.textContent = display;
        g.appendChild(txt);
    }

    _gearPath(radius, teeth, toothH) {
        // Car-style gear: flat-topped teeth with steep sides and rounded valleys
        const rOuter = radius + toothH;
        const rInner = radius - toothH * 0.6;
        const d = [];
        const toothArc = (2 * Math.PI) / teeth;
        // Each tooth: flat top ~40% of arc, steep sides ~10% each, valley ~40%
        const flatFrac = 0.35;
        const sideFrac = 0.08;

        for (let i = 0; i < teeth; i++) {
            const base = toothArc * i - Math.PI / 2;
            // Valley start (inner)
            const a0 = base;
            // Rise start
            const a1 = base + toothArc * sideFrac;
            // Flat top start (outer)
            const a2 = base + toothArc * (0.5 - flatFrac / 2);
            // Flat top end (outer)
            const a3 = base + toothArc * (0.5 + flatFrac / 2);
            // Fall end
            const a4 = base + toothArc * (1 - sideFrac);
            // Valley end (inner) = next tooth's a0
            const a5 = base + toothArc;

            const p = (r, a) => `${(r * Math.cos(a)).toFixed(2)},${(r * Math.sin(a)).toFixed(2)}`;

            if (i === 0) d.push(`M${p(rInner, a0)}`);
            // Rise: inner → outer
            d.push(`L${p(rInner, a1)}`);
            d.push(`L${p(rOuter, a2)}`);
            // Flat top
            d.push(`L${p(rOuter, a3)}`);
            // Fall: outer → inner
            d.push(`L${p(rInner, a4)}`);
            // Valley (slight arc for roundness)
            const midValley = (a4 + a5) / 2;
            const valleyDip = rInner - toothH * 0.15;
            d.push(`Q${(valleyDip * Math.cos(midValley)).toFixed(2)},${(valleyDip * Math.sin(midValley)).toFixed(2)} ${p(rInner, a5)}`);
        }
        d.push('Z');
        return d.join(' ');
    }

    _removeNodeEl(id) {
        const el = this._nels.get(id);
        if (el) { el.remove(); this._nels.delete(id); }
    }

    _removeEdgeEl(key) {
        const el = this._eels.get(key);
        if (el) { el.remove(); this._eels.delete(key); }
    }

    _updateExpandedVisual(node) {
        const el = this._nels.get(node.id);
        if (!el) return;
        const ring = el.querySelector('.fg-ring');
        if (ring) {
            ring.setAttribute('stroke-width', node.expanded ? 2 : 1.2);
            ring.setAttribute('stroke-opacity', node.expanded ? 0.9 : 0.6);
        }
    }

    // ═══════════ Animation Engine ═══════════

    _animate(dur = 500, onDone) {
        if (this._raf) cancelAnimationFrame(this._raf);
        this.animating = true;

        // Snapshot current positions
        for (const n of this.nodes) {
            n._fx = n._rx; n._fy = n._ry;
            n._fo = n._op;
        }

        const t0 = performance.now();

        const tick = (now) => {
            const raw = Math.min(1, (now - t0) / dur);
            // easeInOutCubic
            const ease = raw < 0.5 ? 4 * raw * raw * raw : 1 - Math.pow(-2 * raw + 2, 3) / 2;

            // Interpolate node positions & opacity
            for (const n of this.nodes) {
                const delay = n._delay || 0;
                let t = delay ? Math.max(0, (raw * dur - delay) / (dur - delay)) : raw;
                t = Math.min(1, t);
                const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

                n._rx = n._fx + (n.x - n._fx) * e;
                n._ry = n._fy + (n.y - n._fy) * e;
                n._op = n._fo + (n._targetOp - n._fo) * e;
            }

            // Smooth viewBox
            this._vb.x += (this._vbT.x - this._vb.x) * 0.1;
            this._vb.y += (this._vbT.y - this._vb.y) * 0.1;
            this._vb.w += (this._vbT.w - this._vb.w) * 0.1;
            this._vb.h += (this._vbT.h - this._vb.h) * 0.1;

            this._draw();

            if (raw < 1) {
                this._raf = requestAnimationFrame(tick);
            } else {
                for (const n of this.nodes) {
                    n._rx = n.x; n._ry = n.y; n._op = n._targetOp;
                    delete n._fx; delete n._fy; delete n._fo; delete n._delay;
                }
                this._draw();
                this._raf = null;
                this.animating = false;
                if (onDone) onDone();
            }
        };

        this._raf = requestAnimationFrame(tick);
    }

    _draw() {
        // ViewBox
        this.svg.setAttribute('viewBox',
            `${this._vb.x.toFixed(1)} ${this._vb.y.toFixed(1)} ${this._vb.w.toFixed(1)} ${this._vb.h.toFixed(1)}`);

        // Nodes
        for (const n of this.nodes) {
            const el = this._nels.get(n.id);
            if (!el) continue;
            el.setAttribute('transform', `translate(${n._rx.toFixed(1)},${n._ry.toFixed(1)})`);
            el.style.opacity = String(Math.max(0, n._op).toFixed(3));
        }

        // Edges
        for (const e of this.edges) {
            const el = this._eels.get(e.key);
            const s = this._nmap.get(e.src), t = this._nmap.get(e.tgt);
            if (!el || !s || !t) continue;
            const dx = t._rx - s._rx, dy = t._ry - s._ry;
            const cpx = (s._rx + t._rx) / 2 + (-dy * 0.08);
            const cpy = (s._ry + t._ry) / 2 + (dx * 0.08);
            el.setAttribute('d', `M${s._rx.toFixed(1)},${s._ry.toFixed(1)} Q${cpx.toFixed(1)},${cpy.toFixed(1)} ${t._rx.toFixed(1)},${t._ry.toFixed(1)}`);
            el.style.opacity = String((Math.min(s._op, t._op) * 0.5).toFixed(3));
        }
    }

    // ═══════════ ViewBox Fitting ═══════════

    _fitVB() {
        if (!this.nodes.length) return;
        const pad = 80;
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (const n of this.nodes) {
            if (n._targetOp < 0.1) continue; // skip fading-out nodes
            x0 = Math.min(x0, n.x - n.r - pad);
            y0 = Math.min(y0, n.y - n.r - pad);
            x1 = Math.max(x1, n.x + n.r + pad + 15);
            y1 = Math.max(y1, n.y + n.r + pad);
        }
        // Enforce 3:2 landscape ratio for 6x4 card
        const w = Math.max(x1 - x0, 1000);
        const h = Math.max(y1 - y0, 500);
        this._vbT = { x: x0, y: y0, w, h: Math.min(h, w * 0.65) };
    }

    // After expand: move the expanded node and its children toward vertical center
    _centerOn(node) {
        // Shift the expanded node to vertical middle of canvas
        const targetY = 350;
        const dy = targetY - node.y;
        // Move the node and all its descendants
        node.y = targetY;
        for (const n of this.nodes) {
            if (n.pid === node.id || this._isDescendantOf(n, node.id)) {
                n.y += dy;
            }
        }
        // Re-simulate to settle with the new positions
        this._simulate();
        this._fitVB();
    }

    _isDescendantOf(node, ancestorId) {
        let cur = node;
        while (cur) {
            if (cur.pid === ancestorId) return true;
            cur = this._nmap.get(cur.pid);
        }
        return false;
    }

    // ═══════════ Opacity Targets ═══════════

    _updateOpacities() {
        const anyExpanded = this.nodes.some(n => n.depth === 0 && n.expanded);
        for (const n of this.nodes) {
            if (!anyExpanded) {
                n._targetOp = 1;
            } else {
                // If this node or any ancestor is expanded, stay bright
                n._targetOp = this._isInExpandedBranch(n) ? 1 : 0.25;
            }
        }
    }

    _isInExpandedBranch(node) {
        if (node.expanded) return true;
        if (node.pid == null) return node.expanded;
        const parent = this._nmap.get(node.pid);
        return parent ? this._isInExpandedBranch(parent) : false;
    }

    // ═══════════ Click Handling ═══════════

    _handleClick(node) {
        if (node.depth === 2) {
            this._openExplorer(node);
            return;
        }
        if (node.expanded) {
            this._collapse(node);
            return;
        }

        // Single-branch rule: only when clicking a DIFFERENT root node
        if (node.depth === 0) {
            const expandedRoot = this.nodes.find(n => n.depth === 0 && n.expanded && n.id !== node.id);
            if (expandedRoot) {
                this._collapseSync(expandedRoot);
            }
        }

        this._expand(node);
    }

    _expand(node) {
        const nextDim = this._dimForDepth(node.depth + 1);

        // Build filters from ancestry
        const filters = [...this.config.filters];
        let cur = node;
        const chain = [];
        while (cur) {
            chain.unshift({ dim: cur.dim, label: cur.label });
            cur = this._nmap.get(cur.pid);
        }
        chain.forEach(c => filters.push({ field: c.dim, operator: 'equals', value: c.label }));

        this.animating = true;
        node.expanded = true;
        this._updateExpandedVisual(node);

        ChartAPI.query({
            queryConfig: {
                queryType: 'distribution',
                from: { table: 'assets' },
                select: { dimension: nextDim },
                options: { limit: this.config.maxChildren, sortOrder: 'desc', includeOther: true, includeNull: false },
                filters
            }
        }).then(res => {
            const data = res.data || res;
            if (!data || !Array.isArray(data) || !data.length) {
                node.expanded = false;
                this._updateExpandedVisual(node);
                this.animating = false;
                return;
            }

            const sorted = [...data].sort((a, b) => b.value - a.value).slice(0, this.config.maxChildren);
            const childSizes = this._sizes(sorted, 22, 40);

            // Create children to the RIGHT of parent, fanned out vertically with more spread
            const childCount = sorted.length;
            const fanHeight = Math.min(childCount * 80, 500);
            const fanStartY = node.y - fanHeight / 2 + 40;

            sorted.forEach((d, i) => {
                const xOffset = 220 + (Math.random() - 0.5) * 60;
                const yPos = childCount === 1 ? node.y : fanStartY + (i / (childCount - 1)) * (fanHeight - 80);
                const child = this._addNode({
                    label: d.label, value: d.value, pct: d.percentage,
                    color: d.color || this._color(d.label, nextDim),
                    dim: nextDim, depth: node.depth + 1, pid: node.id,
                    x: node.x + xOffset,
                    y: yPos,
                    r: childSizes[i]
                });
                // Start at parent position, animate out
                child._rx = node._rx; child._ry = node._ry;
                child._op = 0; child._targetOp = 1;
                child._delay = i * 35; // stagger

                this._addEdge(node.id, child.id);
            });

            this._simulate();
            this._createAllElements();
            this._updateOpacities();
            this._centerOn(node);
            this._animate(600, () => {
                this._breadcrumb();
                this._notifyStateChange();
            });

        }).catch(err => {
            console.error('ForceGraph expand error:', err);
            node.expanded = false;
            this._updateExpandedVisual(node);
            this.animating = false;
        });
    }

    _collapse(node) {
        // Collect all descendants
        const desc = new Set();
        const walk = (pid) => {
            for (const n of this.nodes) {
                if (n.pid === pid) { desc.add(n.id); walk(n.id); }
            }
        };
        walk(node.id);

        // Animate descendants back to parent + fade out
        for (const n of this.nodes) {
            if (desc.has(n.id)) {
                n.x = node.x; n.y = node.y; n._targetOp = 0;
            }
        }

        node.expanded = false;
        this._updateOpacities();

        this._animate(350, () => {
            // Remove descendant data + elements
            for (const id of desc) {
                this._removeNodeEl(id);
                this._nmap.delete(id);
            }
            this.nodes = this.nodes.filter(n => !desc.has(n.id));
            const descKeys = new Set();
            this.edges = this.edges.filter(e => {
                if (desc.has(e.src) || desc.has(e.tgt)) { descKeys.add(e.key); return false; }
                return true;
            });
            for (const k of descKeys) this._removeEdgeEl(k);

            this._updateExpandedVisual(node);
            this._simulate();
            this._updateOpacities();
            this._fitVB();
            this._animate(400, () => {
                this._breadcrumb();
                this._notifyStateChange();
            });
        });
    }

    // Instant collapse (no animation) — used when switching to another root
    _collapseSync(node) {
        const desc = new Set();
        const walk = (pid) => {
            for (const n of this.nodes) {
                if (n.pid === pid) { desc.add(n.id); walk(n.id); }
            }
        };
        walk(node.id);

        for (const id of desc) {
            this._removeNodeEl(id);
            this._nmap.delete(id);
        }
        this.nodes = this.nodes.filter(n => !desc.has(n.id));
        const descKeys = new Set();
        this.edges = this.edges.filter(e => {
            if (desc.has(e.src) || desc.has(e.tgt)) { descKeys.add(e.key); return false; }
            return true;
        });
        for (const k of descKeys) this._removeEdgeEl(k);

        node.expanded = false;
        this._updateExpandedVisual(node);
    }

    // ═══════════ Explorer Link ═══════════

    _openExplorer(node) {
        const parts = [];
        let cur = node;
        const chain = [];
        while (cur) {
            chain.unshift({ dim: cur.dim, label: cur.label });
            cur = this._nmap.get(cur.pid);
        }
        for (const c of chain) {
            const v = c.label.includes(' ') ? `"${c.label}"` : c.label;
            // Convert JSONB paths to source__field format for the Explorer
            const field = this._dimToExplorerField(c.dim);
            parts.push(`${field} = ${v}`);
        }
        window.open(`/explorer.html?search=${encodeURIComponent(parts.join(' '))}`, '_blank');
    }

    // Convert a dimension field to Explorer-compatible field name
    // JSONB path: extended_attributes->'SentinelOne'->'agentVersion' → sentinelone__agentVersion
    // Standard: category → category (no change)
    _dimToExplorerField(dim) {
        if (!dim || !dim.includes('->')) return dim;
        const match = dim.match(/extended_attributes->'([^']+)'->'([^']+)'/);
        if (match) {
            return match[1].toLowerCase().replace(/\s+/g, '_') + '__' + match[2];
        }
        return dim;
    }

    // Mark dashboard as dirty when expansion state changes
    _notifyStateChange() {
        if (window.markDashboardDirty) window.markDashboardDirty();
        if (window.markReportAsUnsaved) window.markReportAsUnsaved();
    }

    // ═══════════ Breadcrumb ═══════════

    _breadcrumb() {
        this._bcBar.innerHTML = '';
        const t = this._t();
        const expanded = this.nodes.filter(n => n.expanded).length;

        const lbl = document.createElement('span');
        lbl.style.color = t.sec; lbl.style.fontWeight = '600';
        const dims = [this.config.level1, this.config.level2, this.config.level3]
            .map(d => d.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()));
        lbl.textContent = dims.join(' \u203A ');
        this._bcBar.appendChild(lbl);

        if (expanded > 0) {
            const sep = document.createElement('span');
            sep.style.cssText = `color:${t.sec};margin:0 8px`;
            sep.textContent = '\u2014';
            this._bcBar.appendChild(sep);

            const btn = document.createElement('span');
            btn.style.cssText = `color:${t.sec};cursor:pointer;text-decoration:underline;font-size:10px`;
            btn.textContent = 'Reset';
            btn.addEventListener('click', () => { if (!this.animating && this.data) this.setData(this.data); });
            this._bcBar.appendChild(btn);
        }
    }

    // ═══════════ Helpers ═══════════

    _el(tag, attrs) {
        const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
        for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
        return el;
    }

    _sizes(data, min, max) {
        const vs = data.map(d => d.value);
        const lo = Math.min(...vs), hi = Math.max(...vs), rng = hi - lo || 1;
        return data.map(d => min + ((d.value - lo) / rng) * (max - min));
    }

    _trunc(s, n) { return !s ? '' : s.length > n ? s.slice(0, n - 1) + '\u2026' : s; }

    _fmtN(v) {
        if (v == null) return '';
        if (v >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
        if (v >= 1e3) return (v / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
        return v.toLocaleString();
    }

    destroy() { if (this._raf) cancelAnimationFrame(this._raf); if (this.container) this.container.innerHTML = ''; }
}

if (typeof window !== 'undefined') window.DotDNAForceGraph = DotDNAForceGraph;
if (typeof module !== 'undefined' && module.exports) module.exports = DotDNAForceGraph;
