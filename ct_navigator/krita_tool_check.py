from krita import Krita
view = Krita.instance().activeWindow().activeView()
all_methods = [m for m in dir(view) if not m.startswith('_')]
tool_methods = [m for m in all_methods if 'tool' in m.lower() or 'brush' in m.lower() or 'preset' in m.lower() or 'current' in m.lower() or 'mode' in m.lower() or 'action' in m.lower()]
print("View tool-related methods:")
for m in sorted(tool_methods):
    print("  " + m)

krita_all = [m for m in dir(Krita) if not m.startswith('_')]
krita_tool = [m for m in krita_all if 'tool' in m.lower() or 'brush' in m.lower() or 'preset' in m.lower() or 'current' in m.lower()]
print("\nKrita module tool-related methods:")
for m in sorted(krita_tool):
    print("  " + m)

print("\nChecking specific methods:")
for name in ['currentBrushPreset', 'activeTool', 'currentTool', 'selectedPreset', 'brushPreset', 'currentFgColor', 'currentBgColor']:
    if hasattr(view, name):
        print("  [+] view." + name + " exists")
        try:
            result = getattr(view, name)
            if callable(result):
                val = result()
                print("      value: " + str(val))
            else:
                print("      value: " + str(result))
        except Exception as e:
            print("      error: " + str(e))
    else:
        print("  [-] view." + name + " not found")

canvas = view.canvas()
cm = [m for m in dir(canvas) if 'tool' in m.lower() or 'preset' in m.lower()]
print("\nCanvas tool-related methods:")
for m in sorted(cm):
    print("  " + m)

doc = Krita.instance().activeDocument()
dm = [m for m in dir(doc) if 'tool' in m.lower() or 'preset' in m.lower()]
print("\nDocument tool-related methods:")
for m in sorted(dm):
    print("  " + m)

print("\n--- currentBrushPreset test ---")
try:
    preset = view.currentBrushPreset()
    print("  preset object: " + str(preset))
    print("  preset name: " + str(preset.name()))
except Exception as e:
    print("  error: " + str(e))
