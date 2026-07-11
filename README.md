# Lista de existencias diario

Sitio estatico para Netlify. La pagina lee `inventario.json`, generado desde el PDF diario.

## Actualizar inventario manualmente

```bash
python3 parse_inventory_pdf.py ruta/al/inventario.pdf -o inventario.json
```

Despues de subir el cambio a GitHub, Netlify publica automaticamente.
