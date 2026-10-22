"""Layered graph layout (Sugiyama style) with nested clusters.

Pipeline: break cycles -> assign ranks -> insert dummy nodes for long edges
-> reduce crossings (barycenter sweeps + transpose) -> assign coordinates
(weighted isotonic regression per rank) -> route edges through dummies.

Clusters (subgraphs) are laid out recursively: each cluster becomes one big
node in its parent scope, which guarantees that cluster boxes never overlap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]


@dataclass
class LEdge:
    u: str
    v: str
    minlen: int = 1
    label_w: float = 0.0
    label_h: float = 0.0
    index: int = 0  # position in the caller's edge list
    u_port: float = 0.0  # offset of the attachment point from u's center, across the rank axis
    v_port: float = 0.0


@dataclass
class LResult:
    pos: dict[str, Point]  # item centers
    size: dict[str, tuple[float, float]]
    routes: dict[int, list[Point]]  # edge index -> polyline from u to v (centers at ends)
    labels: dict[int, Point]  # edge index -> label center
    width: float
    height: float


def _isotonic(targets: list[float], weights: list[float]) -> list[float]:
    """Weighted pool-adjacent-violators: non-decreasing fit minimizing squared error."""
    blocks: list[list[float]] = []  # [value, weight, count]
    for t, w in zip(targets, weights):
        blocks.append([t, w, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, c2 = blocks.pop()
            v1, w1, c1 = blocks.pop()
            wt = w1 + w2
            blocks.append([(v1 * w1 + v2 * w2) / wt, wt, c1 + c2])
    out: list[float] = []
    for v, _, c in blocks:
        out.extend([v] * c)
    return out


def layered_layout(items: dict[str, tuple[float, float]], edges: list[LEdge], direction: str = "TB",
                   nodesep: float = 40.0, ranksep: float = 50.0, edgesep: float = 16.0,
                   order_hint: list[str] | None = None, pin_first: set[str] | frozenset = frozenset(),
                   pin_last: set[str] | frozenset = frozenset()) -> LResult:
    """Lay out `items` (id -> (w, h)) connected by `edges`.

    pin_first / pin_last force items onto the first / last rank (used for the
    virtual exit points of edges that leave a cluster).
    """
    direction = direction.upper()
    if direction == "TD":
        direction = "TB"
    horizontal = direction in ("LR", "RL")
    ids = list(order_hint or items.keys())
    ids = [i for i in ids if i in items] + [i for i in items if i not in set(ids)]
    # layout space is always top-to-bottom; for LR/RL swap the node extents
    size = {k: ((h, w) if horizontal else (w, h)) for k, (w, h) in items.items()}

    work = [e for e in edges if e.u != e.v and e.u in items and e.v in items]
    if not ids:
        return LResult({}, {}, {}, {}, 0.0, 0.0)

    # ---- 1. break cycles (DFS in declaration order)
    out_adj: dict[str, list[int]] = {k: [] for k in ids}
    for i, e in enumerate(work):
        out_adj[e.u].append(i)
    reversed_e: set[int] = set()
    state: dict[str, int] = {}
    for root in ids:
        if root in state:
            continue
        stack = [(root, iter(out_adj[root]))]
        state[root] = 1
        while stack:
            node, it = stack[-1]
            for ei in it:
                v = work[ei].v
                s = state.get(v, 0)
                if s == 0:
                    state[v] = 1
                    stack.append((v, iter(out_adj[v])))
                    break
                if s == 1:
                    reversed_e.add(ei)
            else:
                state[node] = 2
                stack.pop()

    def ends(i: int) -> tuple[str, str]:
        e = work[i]
        return (e.v, e.u) if i in reversed_e else (e.u, e.v)

    parallel = set()
    seen_pairs: set[tuple[str, str]] = set()
    for i in range(len(work)):
        a, b = ends(i)
        if (a, b) in seen_pairs:
            parallel.add((a, b))
        seen_pairs.add((a, b))
    double = any(e.label_w > 0 for e in work) or bool(parallel)
    mult = 2 if double else 1

    # ---- 2. ranks: longest path, then pull nodes toward their successors
    preds: dict[str, list[tuple[str, int]]] = {k: [] for k in ids}
    succs: dict[str, list[tuple[str, int]]] = {k: [] for k in ids}
    for i, e in enumerate(work):
        a, b = ends(i)
        ml = max(1, e.minlen) * mult
        preds[b].append((a, ml))
        succs[a].append((b, ml))
    indeg = {k: len(preds[k]) for k in ids}
    topo: list[str] = []
    queue = [k for k in ids if indeg[k] == 0]
    while queue:
        n = queue.pop(0)
        topo.append(n)
        for s, _ in succs[n]:
            indeg[s] -= 1
            if indeg[s] == 0:
                queue.append(s)
    rank: dict[str, int] = {}
    for n in topo:
        rank[n] = max((rank[p] + ml for p, ml in preds[n]), default=0)
    for _ in range(2):
        for n in reversed(topo):
            if succs[n] and len(succs[n]) > len(preds[n]):
                hi = min(rank[s] - ml for s, ml in succs[n])
                lo = max((rank[p] + ml for p, ml in preds[n]), default=-10**9)
                if hi > rank[n] and hi >= lo:
                    rank[n] = hi
    mn = min(rank.values())
    for k in rank:
        rank[k] -= mn
    if pin_first or pin_last:
        core = [k for k in rank if k not in pin_first and k not in pin_last]
        if pin_first:
            for k in core:
                rank[k] += mult
            for k in pin_first:
                if k in rank:
                    rank[k] = 0
        top = max((rank[k] for k in core), default=0)
        for k in pin_last:
            if k in rank:
                rank[k] = top + mult

    # ---- 3. dummy nodes
    node_rank = dict(rank)
    dsize: dict[str, tuple[float, float]] = dict(size)
    is_dummy: dict[str, bool] = {k: False for k in ids}
    chains: dict[int, list[str]] = {}
    label_node: dict[int, str] = {}
    # adjacency entries: (neighbor, weight, port offset at neighbor, port offset at self)
    adj_down: dict[str, list[tuple[str, float, float, float]]] = {k: [] for k in ids}
    adj_up: dict[str, list[tuple[str, float, float, float]]] = {k: [] for k in ids}
    for i in range(len(work)):
        a, b = ends(i)
        ra, rb = node_rank[a], node_rank[b]
        chain = [a]
        e = work[i]
        span = rb - ra
        mid_rank = ra + max(1, span // 2)
        for r in range(ra + 1, rb):
            d = f"__d{i}_{r}"
            node_rank[d] = r
            is_dummy[d] = True
            if e.label_w > 0 and r == mid_rank:
                lw, lh = (e.label_h, e.label_w) if horizontal else (e.label_w, e.label_h)
                dsize[d] = (lw, lh)
                label_node[e.index] = d
            else:
                dsize[d] = (0.0, 0.0)
            adj_down[d] = []
            adj_up[d] = []
            chain.append(d)
        chain.append(b)
        chains[i] = chain
        pa, pb = (e.v_port, e.u_port) if i in reversed_e else (e.u_port, e.v_port)
        for k, (x_, y_) in enumerate(zip(chain, chain[1:])):
            w = 8.0 if (is_dummy[x_] and is_dummy[y_]) else (2.0 if (is_dummy[x_] or is_dummy[y_]) else 1.0)
            px_ = pa if k == 0 else 0.0
            py_ = pb if k == len(chain) - 2 else 0.0
            adj_down[x_].append((y_, w, py_, px_))
            adj_up[y_].append((x_, w, px_, py_))

    max_rank = max(node_rank.values())
    layers: list[list[str]] = [[] for _ in range(max_rank + 1)]

    # ---- 4. initial order: DFS from roots in declaration order
    visited: set[str] = set()
    for root in [k for k in ids if not adj_up[k]] + ids:
        if root in visited:
            continue
        stack = [root]
        while stack:
            n = stack.pop()
            if n in visited:
                continue
            visited.add(n)
            layers[node_rank[n]].append(n)
            for s, *_ in reversed(adj_down[n]):
                if s not in visited:
                    stack.append(s)
    for k in node_rank:
        if k not in visited:
            layers[node_rank[k]].append(k)

    def crossings(lay: list[list[str]]) -> int:
        total = 0
        for r in range(len(lay) - 1):
            pos = {n: i for i, n in enumerate(lay[r + 1])}
            es = []
            for i, n in enumerate(lay[r]):
                for s, _, ps, pn in adj_down[n]:
                    es.append((i + _port_frac(pn, dsize[n][0]), pos[s] + _port_frac(ps, dsize[s][0])))
            es.sort()
            # count inversions of the second coordinate
            seq = [b for _, b in es]
            total += _inversions(seq)
        return total

    best = [list(l) for l in layers]
    best_c = crossings(layers)
    for it in range(20):
        if best_c == 0:
            break
        down = it % 2 == 0
        rng = range(1, max_rank + 1) if down else range(max_rank - 1, -1, -1)
        for r in rng:
            ref = layers[r - 1] if down else layers[r + 1]
            rpos = {n: i for i, n in enumerate(ref)}
            bary = {}
            for i, n in enumerate(layers[r]):
                nb = adj_up[n] if down else adj_down[n]
                if nb:
                    bary[n] = sum(rpos[m] + _port_frac(pm, dsize[m][0]) for m, _, pm, _ in nb) / len(nb)
                else:
                    bary[n] = i
            layers[r].sort(key=lambda n: bary[n])
        _transpose(layers, adj_down, adj_up)
        c = crossings(layers)
        if c < best_c:
            best_c = c
            best = [list(l) for l in layers]
        elif it >= 3 and c > best_c:
            layers = [list(l) for l in best]
    layers = best

    # ---- 5. y coordinates (rank bands)
    band_h = [max((dsize[n][1] for n in layer), default=0.0) for layer in layers]
    sep = ranksep / mult
    ys = []
    y = 0.0
    for r in range(len(layers)):
        if r > 0:
            y += band_h[r - 1] / 2 + sep + band_h[r] / 2
        ys.append(y)

    # ---- 6. x coordinates
    def min_sep(a: str, b: str) -> float:
        gap = nodesep if not (is_dummy[a] or is_dummy[b]) else (edgesep if (is_dummy[a] and is_dummy[b]) else (nodesep + edgesep) / 2)
        if (is_dummy[a] and dsize[a][0] > 0) or (is_dummy[b] and dsize[b][0] > 0):
            gap = max(gap, edgesep)
        return (dsize[a][0] + dsize[b][0]) / 2 + gap

    x: dict[str, float] = {}
    for layer in layers:
        cur = 0.0
        for i, n in enumerate(layer):
            if i > 0:
                cur += min_sep(layer[i - 1], n)
            x[n] = cur

    def place(layer: list[str], neigh: dict[str, list[tuple[str, float]]] | None, both: bool = False) -> None:
        if not layer:
            return
        targets, weights = [], []
        for n in layer:
            nb = (adj_up[n] + adj_down[n]) if both else (neigh[n] if neigh else [])
            if nb:
                tw = sum(w for _, w, _, _ in nb)
                targets.append(sum((x[m] + pm - pn) * w for m, w, pm, pn in nb) / tw)
                weights.append(tw)
            else:
                targets.append(x[n])
                weights.append(0.05)
        offs = [0.0]
        for i in range(1, len(layer)):
            offs.append(offs[-1] + min_sep(layer[i - 1], layer[i]))
        ys_ = _isotonic([t - o for t, o in zip(targets, offs)], weights)
        for n, yv, o in zip(layer, ys_, offs):
            x[n] = yv + o

    for _ in range(10):
        for r in range(1, len(layers)):
            place(layers[r], adj_up)
        for r in range(len(layers) - 2, -1, -1):
            place(layers[r], adj_down)
    for _ in range(3):
        for r in range(len(layers)):
            place(layers[r], None, both=True)

    minx = min(x[n] - dsize[n][0] / 2 for n in x)
    for n in x:
        x[n] -= minx
    width = max(x[n] + dsize[n][0] / 2 for n in x)
    height = ys[-1] + band_h[-1] / 2 + (band_h[0] / 2 if band_h else 0)
    y_off = band_h[0] / 2 if band_h else 0.0

    def to_final(p: Point) -> Point:
        lx, ly = p[0], p[1] + y_off
        if direction == "TB":
            return (lx, ly)
        if direction == "BT":
            return (lx, height - ly)
        if direction == "LR":
            return (ly, lx)
        return (height - ly, lx)  # RL

    pos = {n: to_final((x[n], ys[node_rank[n]])) for n in ids}
    routes: dict[int, list[Point]] = {}
    labels: dict[int, Point] = {}
    for i, chain in chains.items():
        pts = [to_final((x[n], ys[node_rank[n]])) for n in chain]
        if i in reversed_e:
            pts.reverse()
        routes[work[i].index] = pts
    for ei, d in label_node.items():
        labels[ei] = to_final((x[d], ys[node_rank[d]]))
    fw, fh = (height, width) if horizontal else (width, height)
    return LResult(pos, dict(items), routes, labels, fw, fh)


def _port_frac(port: float, width: float) -> float:
    if not port or width <= 0:
        return 0.0
    return max(-0.45, min(0.45, port / width))


def _inversions(seq: list[int]) -> int:
    if len(seq) < 2:
        return 0
    # merge-sort inversion count
    def sort(a):
        if len(a) <= 1:
            return a, 0
        m = len(a) // 2
        l, cl = sort(a[:m])
        r, cr = sort(a[m:])
        out, i, j, c = [], 0, 0, cl + cr
        while i < len(l) and j < len(r):
            if r[j] < l[i]:
                out.append(r[j])
                c += len(l) - i
                j += 1
            else:
                out.append(l[i])
                i += 1
        out += l[i:] + r[j:]
        return out, c
    return sort(seq)[1]


def _transpose(layers, adj_down, adj_up) -> None:
    """Swap adjacent nodes when that reduces crossings with neighbor layers."""
    def cross_pair(u, v, r):
        c = 0
        for adj, rr in ((adj_up, r - 1), (adj_down, r + 1)):
            if rr < 0 or rr >= len(layers):
                continue
            pos = {n: i for i, n in enumerate(layers[rr])}
            pu = [pos[m] for m, *_ in adj[u] if m in pos]
            pv = [pos[m] for m, *_ in adj[v] if m in pos]
            for a in pu:
                for b in pv:
                    if a > b:
                        c += 1
        return c

    for _ in range(3):
        improved = False
        for r, layer in enumerate(layers):
            for i in range(len(layer) - 1):
                u, v = layer[i], layer[i + 1]
                if cross_pair(u, v, r) > cross_pair(v, u, r):
                    layer[i], layer[i + 1] = v, u
                    improved = True
        if not improved:
            break


# --------------------------------------------------------------------------- compound layout


@dataclass
class LCluster:
    id: str
    nodes: list[str] = field(default_factory=list)
    clusters: list["LCluster"] = field(default_factory=list)
    direction: str | None = None
    title_w: float = 0.0
    title_h: float = 0.0


@dataclass
class CompoundResult:
    pos: dict[str, Point]  # node centers (absolute)
    clusters: dict[str, tuple[float, float, float, float]]  # x, y, w, h
    routes: dict[int, list[Point]]
    labels: dict[int, Point]
    width: float
    height: float


CLUSTER_PAD = 18.0


def compound_layout(sizes: dict[str, tuple[float, float]], edges: list[LEdge], top_nodes: list[str],
                    top_clusters: list[LCluster], direction: str = "TB", nodesep: float = 40.0,
                    ranksep: float = 50.0) -> CompoundResult:
    """Lay out a graph with nested clusters.

    Runs twice: the first pass finds where edges that leave a cluster need to
    go; the second pass adds virtual exit points on the matching cluster side
    so those edges are routed around the cluster's other nodes.
    """
    first = _compound_pass(sizes, edges, top_nodes, top_clusters, direction, nodesep, ranksep, {})
    anchors = _plan_anchors(first, edges, direction)
    if not anchors:
        return first.result
    return _compound_pass(sizes, edges, top_nodes, top_clusters, direction, nodesep, ranksep, anchors).result


@dataclass
class _Pass:
    result: CompoundResult
    parent: dict[str, str | None]
    scope_of: dict[int, str | None]
    member_of: dict[str, list[str]]  # cluster -> direct members (nodes and sub-clusters)
    cluster_dir: dict[str, str]


# anchor plan: cluster id -> list of (rep item id, side 'first'|'last', edge index, end 'u'|'v')
_Anchors = dict[str, list[tuple[str, str, int, str]]]


def _rank_axis(d: str) -> tuple[int, int]:
    """(axis index, sign) of the rank direction: TB -> (1, +1), BT -> (1, -1), LR -> (0, +1), RL -> (0, -1)."""
    return {"TB": (1, 1), "TD": (1, 1), "BT": (1, -1), "LR": (0, 1), "RL": (0, -1)}[d.upper()]


def _plan_anchors(p: _Pass, edges: list[LEdge], direction: str) -> _Anchors:
    res = p.result
    anchors: _Anchors = {}

    def center(item: str) -> Point | None:
        if item in res.pos:
            return res.pos[item]
        if item in res.clusters:
            x, y, w, h = res.clusters[item]
            return (x + w / 2, y + h / 2)
        return None

    def chain(x: str) -> list[str]:
        out = []
        q = p.parent.get(x)
        while q is not None:
            out.append(q)
            q = p.parent.get(q)
        return out  # innermost first

    for e in edges:
        if e.index not in res.routes:
            continue
        scope = p.scope_of.get(e.index)
        for end, n, other in (("u", e.u, e.v), ("v", e.v, e.u)):
            other_c = center(other)
            if other_c is None:
                continue
            rep = n
            for cl in chain(n):
                if cl == scope:
                    break
                x, y, w, h = res.clusters[cl]
                axis, sign = _rank_axis(p.cluster_dir[cl])
                lo, hi = (x, x + w) if axis == 0 else (y, y + h)
                o = other_c[axis]
                if o > hi:
                    side = "last" if sign > 0 else "first"
                elif o < lo:
                    side = "first" if sign > 0 else "last"
                else:
                    rep = cl
                    continue
                rc = center(rep)
                beyond = False
                for m in p.member_of.get(cl, []):
                    if m == rep:
                        continue
                    mc = center(m)
                    if mc is None:
                        continue
                    if (mc[axis] - rc[axis]) * (1 if o > hi else -1) > 1.0:
                        beyond = True
                        break
                if beyond:
                    anchors.setdefault(cl, []).append((rep, side, e.index, end))
                rep = cl
    return anchors


def _compound_pass(sizes, edges, top_nodes, top_clusters, direction, nodesep, ranksep, anchors: _Anchors) -> _Pass:
    parent: dict[str, str | None] = {}
    member_of: dict[str, list[str]] = {}
    cluster_dir: dict[str, str] = {}

    def walk(cl: LCluster, par: str | None, d: str):
        parent[cl.id] = par
        dd = cl.direction or d
        cluster_dir[cl.id] = dd
        member_of[cl.id] = list(cl.nodes) + [c.id for c in cl.clusters]
        for n in cl.nodes:
            parent[n] = cl.id
        for c in cl.clusters:
            walk(c, cl.id, dd)

    for n in top_nodes:
        parent[n] = None
    for c in top_clusters:
        walk(c, None, direction)

    def chain(x: str) -> list[str]:
        out = []
        q = parent.get(x)
        while q is not None:
            out.append(q)
            q = parent.get(q)
        return list(reversed(out))  # root-most first

    # scope of every edge: deepest common cluster of its endpoints
    scope_edges: dict[str | None, list[tuple[LEdge, str, str]]] = {}
    scope_of: dict[int, str | None] = {}
    for e in edges:
        if e.u not in parent or e.v not in parent:
            continue
        cu, cv = chain(e.u) + [e.u], chain(e.v) + [e.v]
        k = 0
        while k < min(len(cu), len(cv)) and cu[k] == cv[k]:
            k += 1
        if k >= len(cu) or k >= len(cv):
            continue  # edge between a cluster and its own descendant
        scope = cu[k - 1] if k > 0 else None
        scope_edges.setdefault(scope, []).append((e, cu[k], cv[k]))
        scope_of[e.index] = scope

    anchor_keys: dict[tuple[int, str, str], int] = {}  # (edge, end, cluster) -> anchor route index
    for cl, lst in anchors.items():
        for rep, side, ei, end in lst:
            anchor_keys[(ei, end, cl)] = -1 - len(anchor_keys)

    def crossing(e: LEdge, end: str, cl: str) -> bool:
        """Does edge e leave cluster cl at endpoint `end`?"""
        n = e.u if end == "u" else e.v
        ch = chain(n)
        if cl not in ch:
            return False
        sc = scope_of.get(e.index, "__none__")
        if sc == "__none__":
            return False
        return sc is None or (sc in ch and ch.index(sc) < ch.index(cl))

    def lay(scope_id: str | None, nodes: list[str], clusters: list[LCluster], dirn: str) -> dict:
        """Lay out one scope; returns local geometry plus exit points of edges leaving it."""
        cross_axis = 0 if dirn.upper() in ("TB", "TD", "BT") else 1
        items: dict[str, tuple[float, float]] = {}
        sub: dict[str, tuple] = {}
        order: list[str] = []
        for n in nodes:
            items[n] = sizes[n]
            order.append(n)
        for c in clusters:
            r = lay(c.id, c.nodes, c.clusters, c.direction or dirn)
            w = max(r["w"], c.title_w + 8) + 2 * CLUSTER_PAD
            h = r["h"] + 2 * CLUSTER_PAD + c.title_h
            items[c.id] = (w, h)
            # offset of the sub-layout's origin relative to the block's top-left corner
            sub[c.id] = (r, c, CLUSTER_PAD + (w - 2 * CLUSTER_PAD - r["w"]) / 2, CLUSTER_PAD + c.title_h)
            order.append(c.id)

        def port(item: str, ei: int, end: str) -> float:
            if item not in sub:
                return 0.0
            r, c, ox, oy = sub[item]
            ex = r["exits"].get((ei, end))
            if ex is None:
                return 0.0
            w, h = items[item]
            return (ex[0] + ox - w / 2) if cross_axis == 0 else (ex[1] + oy - h / 2)

        les = []
        for e, ru, rv in scope_edges.get(scope_id, []):
            if ru in items and rv in items:
                les.append(LEdge(ru, rv, e.minlen, e.label_w, e.label_h, e.index,
                                 port(ru, e.index, "u"), port(rv, e.index, "v")))
        pin_first, pin_last = set(), set()
        anchor_items: dict[tuple[int, str], str] = {}
        for rep, side, ei, end in anchors.get(scope_id, []) if scope_id is not None else []:
            if rep not in items:
                continue
            key = anchor_keys[(ei, end, scope_id)]
            aid = f"__anchor{-key}"
            items[aid] = (0.0, 0.0)
            anchor_items[(ei, end)] = aid
            p_rep = port(rep, ei, end)
            if side == "last":
                les.append(LEdge(rep, aid, 1, 0.0, 0.0, key, p_rep, 0.0))
                pin_last.add(aid)
            else:
                les.append(LEdge(aid, rep, 1, 0.0, 0.0, key, 0.0, p_rep))
                pin_first.add(aid)
        res = layered_layout(items, les, dirn, nodesep, ranksep, order_hint=order,
                             pin_first=pin_first, pin_last=pin_last)
        pos: dict[str, Point] = {}
        boxes: dict[str, tuple[float, float, float, float]] = {}
        for n in nodes:
            pos[n] = res.pos[n]
        for cid, (r, c, ox_rel, oy_rel) in sub.items():
            cx, cy = res.pos[cid]
            w, h = items[cid]
            x0, y0 = cx - w / 2, cy - h / 2
            boxes[cid] = (x0, y0, w, h)
            ox, oy = x0 + ox_rel, y0 + oy_rel
            for k, (px, py) in r["pos"].items():
                pos[k] = (px + ox, py + oy)
            for k, (bx, by, bw, bh) in r["boxes"].items():
                boxes[k] = (bx + ox, by + oy, bw, bh)
            for k, pts in r["routes"].items():
                res.routes[k] = [(px + ox, py + oy) for px, py in pts]
            for k, (px, py) in r["labels"].items():
                res.labels[k] = (px + ox, py + oy)

        # exit points (local coordinates) of edges that leave this scope
        exits: dict[tuple[int, str], Point] = {}
        if scope_id is not None:
            for e in edges:
                for end, n in (("u", e.u), ("v", e.v)):
                    if not crossing(e, end, scope_id):
                        continue
                    aid = anchor_items.get((e.index, end))
                    if aid is not None:
                        exits[(e.index, end)] = res.pos[aid]
                        continue
                    ch = chain(n) + [n]
                    rep = ch[ch.index(scope_id) + 1]
                    if rep in sub:
                        r, c, ox_rel, oy_rel = sub[rep]
                        ex = r["exits"].get((e.index, end))
                        bx, by, _, _ = boxes[rep]
                        if ex is not None:
                            exits[(e.index, end)] = (ex[0] + bx + ox_rel, ex[1] + by + oy_rel)
                            continue
                    if rep in pos:
                        exits[(e.index, end)] = pos[rep]
                    elif rep in boxes:
                        bx, by, bw, bh = boxes[rep]
                        exits[(e.index, end)] = (bx + bw / 2, by + bh / 2)
        return {"w": res.width, "h": res.height, "pos": pos, "boxes": boxes, "routes": res.routes,
                "labels": res.labels, "exits": exits}

    top = lay(None, top_nodes, top_clusters, direction)
    w, h, pos, boxes, routes, labels = top["w"], top["h"], top["pos"], top["boxes"], top["routes"], top["labels"]

    def item_center(item: str) -> Point:
        if item in pos:
            return pos[item]
        bx, by, bw, bh = boxes[item]
        return (bx + bw / 2, by + bh / 2)

    final_routes: dict[int, list[Point]] = {}
    for e in edges:
        main = routes.get(e.index)
        if not main:
            continue
        main = list(main)

        def pieces(n: str, end: str) -> list[list[Point]]:
            """Anchor routes from the node outwards (innermost cluster first), each oriented outward."""
            out = []
            for cl in reversed(chain(n)):
                if cl == scope_of.get(e.index):
                    break
                key = anchor_keys.get((e.index, end, cl))
                if key is None or key not in routes:
                    continue
                pts = list(routes[key])
                rep_side = [a for a in anchors[cl] if a[2] == e.index and a[3] == end][0]
                if rep_side[1] == "first":
                    pts.reverse()  # stored as anchor -> rep
                out.append(pts)
            return out

        start_pts: list[Point] = [item_center(e.u)]
        for pc in pieces(e.u, "u"):
            start_pts += pc[1:]
        end_pts: list[Point] = [item_center(e.v)]
        for pc in pieces(e.v, "v"):
            end_pts += pc[1:]
        end_pts.reverse()
        final_routes[e.index] = start_pts + main[1:-1] + end_pts
    return _Pass(CompoundResult(pos, boxes, final_routes, labels, w, h), parent, scope_of, member_of, cluster_dir)
