import os
import json
import random
import string
import platform

TARGET_DIR = r"C:\Users\danie\Documents\BioWare\Mass Effect Andromeda\Insurance Review"
MANIFEST_PATH = os.path.join(TARGET_DIR, ".sys_config_map.json")

def generate_system_name(index, orig_ext=""):
    # Genera nombres aburridos de Windows manteniendo la extension original
    prefixes = ["sys_cache_log_", "win_temp_node_", "reg_backup_x64_", "dump_err_", "ms_data_"]
    prefix = random.choice(prefixes)
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}{suffix}_{index}{orig_ext}"

def lock_folder():
    if not os.path.exists(TARGET_DIR):
        print(f"[!] Error: La carpeta no existe -> {TARGET_DIR}")
        return

    if os.path.exists(MANIFEST_PATH):
        print("[!] La bóveda ya parece estar CERRADA.")
        return

    mapping = [] # Lista mantiene orden de inserción
    index = 1
    
    print("\n[+] Iniciando Modo Bóveda (Extensiones Originales)...")
    
    # Recorrer de ABAJO hacia ARRIBA (Bottom-up)
    for root, dirs, files in os.walk(TARGET_DIR, topdown=False):
        # 1. Renombrar Archivos
        for file in files:
            if file.startswith('.') or file == "desktop.ini" or file == "vault_system.py":
                continue
                
            original_path = os.path.join(root, file)
            _, ext = os.path.splitext(file)
            
            fake_name = generate_system_name(index, ext)
            fake_path = os.path.join(root, fake_name)
            
            try:
                os.rename(original_path, fake_path)
                rel_fake = os.path.relpath(fake_path, TARGET_DIR)
                rel_orig = os.path.relpath(original_path, TARGET_DIR)
                mapping.append({"fake": rel_fake, "orig": rel_orig})
                index += 1
            except Exception as e:
                pass
                
        # 2. Renombrar Carpetas (Bottom-up asegura que no corromperemos rutas de los archivos hijos)
        for d in dirs:
            original_dir_path = os.path.join(root, d)
            fake_dir_name = generate_system_name(index, "") # Sin extensión
            fake_dir_path = os.path.join(root, fake_dir_name)
            
            try:
                os.rename(original_dir_path, fake_dir_path)
                rel_fake = os.path.relpath(fake_dir_path, TARGET_DIR)
                rel_orig = os.path.relpath(original_dir_path, TARGET_DIR)
                mapping.append({"fake": rel_fake, "orig": rel_orig})
                index += 1
            except Exception as e:
                pass

    # Guardar mapa
    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, indent=4)
        
    if platform.system() == "Windows":
        os.system(f'attrib +h "{MANIFEST_PATH}"')

    print(f"\n[EXITO] Boveda cerrada. Se camuflaron {len(mapping)} elementos.")
    print("   Las fotos siguen siendo JPG y se pueden ver, pero sus nombres (y los de sus carpetas) estan enmascarados.")

def unlock_folder():
    if not os.path.exists(MANIFEST_PATH):
        print("[!] No se encontro el registro. La carpeta ya esta abierta?")
        return

    print("\n[+] Abriendo Boveda, restaurando rutas y nombres originales...")
        
    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        mapping = json.load(f)
        
    restored = 0
    # RESTAURACION INVERSA (De arriba hacia abajo estructuralmente hablando)
    for item in reversed(mapping):
        rel_fake = item["fake"]
        rel_orig = item["orig"]
        
        fake_path = os.path.join(TARGET_DIR, rel_fake)
        orig_path = os.path.join(TARGET_DIR, rel_orig)
        
        if os.path.exists(fake_path):
            try:
                os.rename(fake_path, orig_path)
                restored += 1
            except Exception as e:
                pass
                
    if restored >= len(mapping) * 0.9:
        os.remove(MANIFEST_PATH)
        print(f"\n[EXITO] Boveda abierta. {restored} elementos (carpetas y archivos) restaurados.")
    else:
        print("\n[ADVERTENCIA] Hubo problemas restaurando algunos elementos.")

if __name__ == "__main__":
    print("=== MINGCORP VAULT SYSTEM (PREVIEW MODE) ===")
    print("1. Cerrar Boveda (Camuflar sin tocar extension)")
    print("2. Abrir Boveda (Restaurar originales)")
    
    choice = input("Selecciona: ").strip()
    
    if choice == "1":
        lock_folder()
    elif choice == "2":
        unlock_folder()
