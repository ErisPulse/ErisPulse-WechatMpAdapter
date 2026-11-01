[English](README.en.md) | 简体中文 | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md) | [Русский](README.ru.md)

<div align="center">

<img src="https://raw.githubusercontent.com/ErisPulse/ErisPulse/main/.github/assets/ErisPulseLogo.png" width="180" alt="ErisPulse-WechatMpAdapter" />

# ErisPulse-WechatMpAdapter

**ErisPulse WeChat 公式アカウントアダプター — Webhook + セカンダリー/テンプレートメッセージ**

<p>
  <a href="https://pypi.org/project/ErisPulse-WechatMpAdapter/"><img src="https://img.shields.io/pypi/v/ErisPulse-WechatMpAdapter?style=for-the-badge&logo=pypi&logoColor=white" alt="PyPI"></a>
  <a href="https://pypi.org/project/ErisPulse-WechatMpAdapter/"><img src="https://img.shields.io/badge/Python-3.10+-FFD43B?style=for-the-badge&logo=python&logoColor=blue" alt="Python"></a>
  <a href="https://github.com/ErisPulse/ErisPulse/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue?style=for-the-badge" alt="License"></a>
  <img src="https://raw.githubusercontent.com/ErisPulse/.github/main/assets/badges/adapter.svg" alt="ErisPulse">
</p>

</div>

---

## Features

- **Multi-account** — one official account per entry, independent config and callback path
- **Encryption** — plain / compatible / safe modes handled, AES-256-CBC decryption included
- **Signature verification** — both GET handshake and POST message callbacks verified
- **access_token management** — auto fetch, cache and refresh 5 minutes early
- **Full message types** — text, image, voice, video, news, music, template and menu messages
- **OneBot12 standard** — strict event conversion; platform extensions prefixed `mp_`

## Install

```bash
epsdk install WechatMpAdapter
```

## Links

- [ErisPulse main repo](https://github.com/ErisPulse/ErisPulse)
- [Module development guide](https://www.erisdev.com/#docs/developer-guide/README.md)

> The full documentation (configuration tables, event mappings, API reference) is in Chinese: switch to **简体中文** above.

## License

MIT
