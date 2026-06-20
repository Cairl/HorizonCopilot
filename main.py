"""ForzaHorizon6Copilot — 地平线 6 辅助工具."""

import sys

from udlrtui import C, K, Renderer, Navigator, widgets as W
from udlrtui import init_console, get_key

MENU_ITEMS = [
    {"label": "拍卖行抢车", "tag": "auction"},
]


def render_menu(nav: Navigator, renderer: Renderer) -> None:
    W_VAL = 32
    lines = [W.top_border("ForzaHorizon6Copilot", W_VAL)]
    for i, item in enumerate(MENU_ITEMS):
        label = f"{C.WHITE}{item['label']}{C.RESET}"
        lines.append(W.line_sel(label, W_VAL) if nav.index == i else W.line(label, W_VAL))
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
                from tasks.auction import run_auction_sniper
                run_auction_sniper(renderer)


if __name__ == "__main__":
    main()
