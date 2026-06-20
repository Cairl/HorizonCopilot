from .sniper import run_auction_sniper, AuctionTask
from .feature_store import FeatureSlot, FeatureStore

task_info = {
    "label": "拍卖场抢车",
    "tag": "auction",
    "task_class": AuctionTask,
}
