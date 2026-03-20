import sys
import os
from PyQt5.QtWidgets import QApplication

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from thermobath.ui import ThermobathUI

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ThermobathUI()
    window.show()
    sys.exit(app.exec_())
