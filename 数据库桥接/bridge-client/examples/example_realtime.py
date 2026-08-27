"""实时行情示例：装饰器注册回调，WebSocket 自动重连。

先在 Mac 上运行本脚本；如服务端尚未订阅对应代码，
客户端会自动向桥接机补发订阅请求（幂等）。
Ctrl+C 退出。
"""
import logging
import os

from galaxy_bridge import GalaxyBridgeClient, RealtimeSubscriber

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")

URL = os.environ.get("BRIDGE_URL", "http://192.168.1.50:8900")
KEY = os.environ.get("BRIDGE_API_KEY")

client = GalaxyBridgeClient(URL, api_key=KEY)
sub = RealtimeSubscriber(client)


@sub.register(code_list=["510300.SH", "000001.SZ"], period="snapshot")
def on_snapshot(data, period):
    # data 为 SimpleNamespace：字段见手册附录 Snapshot 结构
    d = vars(data)
    print(f"[{period}] {d.get('security_code', '?'):>10} "
          f"last={d.get('last_price', d.get('last'))} "
          f"vol={d.get('volume_trade', d.get('volume'))}")


@sub.register(code_list=["510300.SH"], period="min1")
def on_min1(data, period):
    d = vars(data)
    print(f"[{period}] {d.get('security_code', '?')} O={d.get('open_price')} "
          f"H={d.get('high_price')} L={d.get('low_price')} C={d.get('close_price')}")


if __name__ == "__main__":
    print("等待推送...（交易时段才有实时数据；可用 sub_add 预先在服务端登记订阅）")
    sub.run(block=True)
