"""
List of mediawiki namespaces per language.
Auto-generated with `python -m scripts`.
"""

# START
namespaces = {
    "ca": ["Categoria", "Fitxer", "Image", "Imatge"],
    "da": ["Billede", "Fil", "Image", "Kategori"],
    "de": ["Bild", "Datei", "Image", "Kategorie"],
    "el": ["Image", "Αρχείο", "Εικόνα", "Κατηγορία"],
    "en": ["CAT", "Category", "File", "Image"],
    "eo": ["Dosiero", "Image", "Kategorio"],
    "es": ["Archivo", "Categoría", "Image", "Imagen"],
    "fr": ["Catégorie", "Fichier", "Image"],
    "it": ["Categoria", "File", "Image", "Immagine"],
    "ja": ["Image", "カテゴリ", "ファイル", "画像"],
    "lt": ["Image", "Kategorija", "Vaizdas"],
    "no": ["Bilde", "Fil", "Image", "Kategori"],
    "pt": ["Arquivo", "Categoria", "Ficheiro", "Image", "Imagem"],
    "ro": ["Categorie", "Fişier", "Fișier", "Image", "Imagine"],
    "ru": ["Image", "Изображение", "К", "Категория", "Файл"],
    "sv": ["Bild", "Fil", "Image", "KAT", "Kategori"],
    "zh": ["CAT", "Category", "File", "Image", "分类", "分類", "图像", "图片", "圖像", "圖片", "文件", "档案", "檔案"],
}
# END

# Auto-populate namespaces for engrish.json derived languages (all use EN Wiktionary namespaces)
import json
from pathlib import Path

_engrish_json = Path(__file__).parent.parent / "engrish" / "engrish.json"
if _engrish_json.exists():
    for _code in json.loads(_engrish_json.read_text(encoding="utf-8")).get("languages", {}):
        if _code not in namespaces and "en" in namespaces:
            namespaces[_code] = list(namespaces["en"])
