# A组共享发现（R1）

- 交易所目录身份与逐只产品通道证据分离；全局目录只使用单一evidence_id。
- 上轮21条OUT_OF_SCOPE逐只尝试官方产品资料；无法核验的候选改待核实，不再沿用旧classification。
- 513090已完成真实PCF数量→1分钟篮子→60日滚动训练（50拟合+10验证）→候选选择→OOS残差探索，旧窗口不升级为确认。
- 01788数量保留；停牌冻结只对该证券缺价使用停牌前收盘，其他证券缺价不填。
- 详见assets.json、research/513090_panel_1min.csv、research/513090_oos_residuals.csv。
