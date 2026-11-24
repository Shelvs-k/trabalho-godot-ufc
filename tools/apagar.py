import os
from pathlib import Path

def limpar_arquivos_import():
    # Identifica exatamente a pasta onde este script .py está localizado
    pasta_do_script = Path(__file__).resolve().parent
    
    # Busca todos os arquivos que terminam com .import (ignorando maiúsculas/minúsculas)
    arquivos_encontrados = [
        f for f in pasta_do_script.iterdir() 
        if f.is_file() and f.suffix.lower() == '.import'
    ]
    
    if not arquivos_encontrados:
        print("Nenhum arquivo .import foi encontrado nesta pasta.")
    else:
        removidos = 0
        for arquivo in arquivos_encontrados:
            try:
                arquivo.unlink()
                print(f"[REMOVIDO] {arquivo.name}")
                removidos += 1
            except Exception as erro:
                print(f"[ERRO] Não foi possível remover {arquivo.name}: {erro}")
                
        print(f"\nConcluído! {removidos} arquivo(s) deletado(s).")

if __name__ == "__main__":
    limpar_arquivos_import()
    # Mantém a janela aberta ao executar com duplo clique
    input("\nPressione ENTER para fechar...")
