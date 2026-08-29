import os
import sys

# Garante que a raiz do projeto esteja no path para importar o pacote "app".
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

# O Vercel Python detecta uma variável WSGI chamada "app" e a expõe como handler.
app = create_app()
