C:\Users\I_Rathnamalala201\AppData\Local\Python\pythoncore-3.14-64\python.exe C:\Users\I_Rathnamalala201\Downloads\ANYGDS2\wafermap_gui.py 
Traceback (most recent call last):
  File "C:\Users\I_Rathnamalala201\Downloads\ANYGDS2\wafermap_gui.py", line 501, in <module>
    App().mainloop()
    ~~~^^
  File "C:\Users\I_Rathnamalala201\Downloads\ANYGDS2\wafermap_gui.py", line 278, in __init__
    self._build()
    ~~~~~~~~~~~^^
  File "C:\Users\I_Rathnamalala201\Downloads\ANYGDS2\wafermap_gui.py", line 302, in _build
    body = tk.Frame(self, bg=BG, padx=12, pady=(0, 12))
  File "C:\Users\I_Rathnamalala201\AppData\Local\Python\pythoncore-3.14-64\Lib\tkinter\__init__.py", line 3353, in __init__
    Widget.__init__(self, master, 'frame', cnf, {}, extra)
    ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\I_Rathnamalala201\AppData\Local\Python\pythoncore-3.14-64\Lib\tkinter\__init__.py", line 2788, in __init__
    self.tk.call(
    ~~~~~~~~~~~~^
        (widgetName, self._w) + extra + self._options(cnf))
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_tkinter.TclError: bad screen distance "0 12"

Process finished with exit code 1
