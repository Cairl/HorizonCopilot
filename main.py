"""ForzaHorizon6Copilot — 地平线 6 辅助工具."""

import sys

from udlrtui import C, K, Renderer, Navigator, widgets as W
from udlrtui import init_console, get_key

# ── 菜单项定义 ──────────────────────────────────────────────
MENU_ITEMS = [
    {"label": "拍卖场自动抢车", "tag": "auction"},
]


def render_menu(nav: Navigator, renderer: Renderer) -> None:
    W_VAL = 36
    lines = [W.top_border("ForzaHorizon6Copilot", W_VAL)]

    for i, item in enumerate(MENU_ITEMS):
        label = f"{C.WHITE}{item['label']}{C.RESET}"
        if nav.index == i:
            lines.append(W.line_sel(label, W_VAL))
        else:
            lines.append(W.line(label, W_VAL))

    lines.append(W.bottom_border(W_VAL))
    renderer.render(lines)


def main() -> None:
    init_console()
    renderer = Renderer()
    nav = Navigator(n_items=len(MENU_ITEMS))

    while True:
        render_menu(nav, renderer)
        key = get_key()

        if key == K.ESC:
            break
        if nav.handle(key):
            continue
        if key == K.ENTER:
            item = MENU_ITEMS[nav.index]
            if item["tag"] == "auction":
                run_auction_sniper(renderer)


# ── 拍卖场自动抢车 ──────────────────────────────────────────

def run_auction_sniper(renderer: Renderer) -> None:
    """拍卖场自动抢车 — 占位，待实现。"""
    renderer.reset()
    W_VAL = 44
    lines = [
        W.top_border("拍卖场自动抢车", W_VAL),
        W.line(f"{C.YELLOW}功能开发中...{C.RESET}", W_VAL),
        W.line("", W_VAL),
        W.line(f"{C.GRAY}按 Esc 返回主菜单{C.RESET}", W_VAL),
        W.bottom_border(W_VAL),
    ]
    renderer.render(lines)

    while True:
        key = get_key()
        if key == K.ESC:
            renderer.reset()
            return


if __name__ == "__main__":
    main()
