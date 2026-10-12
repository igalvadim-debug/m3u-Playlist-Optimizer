import sys
sys.path.insert(0, r"D:\m3uGenius\m3uGenius")

print("="*60)
print("ТЕСТ ОБНОВЛЕННОГО M3UTester")
print("="*60)

try:
    from modules.tester import M3UTester
    print("✓ Модуль M3UTester импортирован успешно")
    
    # Создаем экземпляр (это проверит FFmpeg)
    tester = M3UTester(timeout=5, max_workers=10)
    print("✓ M3UTester инициализирован (FFmpeg проверен)")
    
    print("\n📊 Настройки:")
    print(f"  - Timeout: {tester.timeout} сек")
    print(f"  - Max workers: {tester.max_workers}")
    print(f"  - Статистика инициализирована: {bool(tester.stats)}")
    
    print("\n✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ!")
    print("Модуль готов к использованию в Gradio")
    
except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
