# 只读厂商契约参考

- `manuals/`：AmazingData 与 TGW C++ 开发手册。
- `vendor-headers/v1.0.8/`：Linux/Windows 发行包的公开 C++ 头文件。

这些文件只用于核对公开 ABI、字段和回调语义，不参与 Mac 构建。x86_64 `.so`、Windows
`.dll/.pyd`、PDB、反编译缓存和原始抓包没有迁入本工程；需要动态行为时使用授权 Linux
官方 SDK oracle，按 `docs/AGENT_PARITY_WORKFLOW.md` 脱敏取证。
