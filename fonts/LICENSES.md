# Bundled fonts & licenses

| File | Font | License | Full text |
|---|---|---|---|
| ipaexg.ttf | IPAexゴシック | IPA Font License v1.0 | `IPA_Font_License_Agreement_v1.0.txt`（同梱・日本語/英語併記） |
| ipaexm.ttf | IPAex明朝 | IPA Font License v1.0 | 同上 |
| NotoSansCJKjp-Regular.otf | Noto Sans CJK JP | SIL OFL 1.1 | `OFL.txt`（本文＋Copyright行） |
| LiberationSerif-*.ttf | Liberation Serif (v2.1.5, OFL版) | SIL OFL 1.1 | `OFL.txt` |
| LiberationSans-Regular.ttf | Liberation Sans (v2.1.5, OFL版) | SIL OFL 1.1 | `OFL.txt` |

## 再配布条件の充足について

- **IPAex**: IPAフォントライセンスv1.0 第3条2項(3)「本契約の写しを添付」→ `IPA_Font_License_Agreement_v1.0.txt` を本ディレクトリに同梱（正式配布zip `IPAexfont00401.zip` から同一名で抽出した原本）。名称変更・改変なし。
- **Noto / Liberation (OFL)**: 著作権表示とライセンス本文を各コピーに同梱 → `OFL.txt` に本文＋各フォントのCopyright行（フォントname table 0番から実取得: Adobe © 2014-2021 / Google+Red Hat © 2010,2012）。Reserved Font Name の扱いもOFL本文どおり。
- **tessdata** (`../tools/tessdata/`): Apache-2.0 第4条(a) ライセンス写し → `../tools/tessdata/LICENSE`。

Microsoft fonts (msgothic.ttc / msmincho.ttc / meiryo.ttc / YuGoth*.ttc / times.ttf / arial.ttf / calibri.ttf / century.ttf) are **referenced from the system font directory at runtime and never redistributed** with this project.

Third-party notices for the Python engine: see `../THIRD-PARTY-NOTICES.md`.
