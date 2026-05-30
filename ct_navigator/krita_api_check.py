from krita import Krita
view = Krita.instance().activeWindow().activeView()
methods = [m for m in dir(view) if not m.startswith('_')]
color_methods = [m for m in methods if 'color' in m.lower() or 'fore' in m.lower() or 'back' in m.lower()]
print("View color methods:")
for m in sorted(color_methods):
    print("  " + m)
managed = view.foregroundColor()
mm = [m for m in dir(managed) if not m.startswith('_')]
print("\nManagedColor methods:")
for m in sorted(mm):
    print("  " + m)
print("\nCheck setForeGroundColor:")
print("  found=" + str(hasattr(view, 'setForeGroundColor')))
print("  found=" + str(hasattr(view, 'setForegroundColor')))
print("  found=" + str(hasattr(managed, 'setComponents')))
