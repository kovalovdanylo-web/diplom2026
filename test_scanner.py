"""
Тестування розпізнавання без запуску бота.
Запуск: python test_scanner.py test_images/klas.jpg
"""

import sys
import json

from utils.claude_scanner import scan_receipt


def test_image(path: str):
    print(f"📷 Обробка: {path}")
    print("-" * 40)

    with open(path, "rb") as f:
        image_bytes = f.read()

    print(f"📦 Розмір файлу: {len(image_bytes) / 1024:.1f} KB")

    result = scan_receipt(image_bytes)

    if result["success"]:
        print(f"✅ Знайдено чеків: {len(result['receipts'])}")
        for i, r in enumerate(result["receipts"], 1):
            print(f"\n📄 Чек {i}:")
            print(f"  Номер:  {r['receipt_number']}")
            print(f"  ФН:     {r['fiscal_number']}")
            print(f"  ЗН:     {r['serial_number']}")
            print(f"  Дата:   {r['receipt_date']} {r['receipt_time']}")
            print(f"  Сума:   {r['amount']} грн")
            print(f"  QR:     {r['qr_link']}")

        print(f"\n📊 Токени: {result['input_tokens']}↑ {result['output_tokens']}↓")
        print(f"💾 Raw JSON: {result['raw_response']}")
    else:
        print(f"❌ Помилка: {result['error']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Використання: python test_scanner.py шлях_до_фото.jpg")
        sys.exit(1)

    for path in sys.argv[1:]:
        test_image(path)
        print()
