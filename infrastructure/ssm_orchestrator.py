import os
import boto3
import time
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "YOUR_AWS_KEY_ID")
SECRET_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', 'mock_aws_secret_key')AWS_SECRET_ACCESS_KEY', 'mock_aws_secret_key')"
REGION = "eu-west-2"
INSTANCE_ID = "i-0123456789abcdef0"

ssm = boto3.client('ssm', region_name=REGION, aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY)

def wait_for_ssm(max_attempts=20, delay=10):
    print("Verificando registro del agente SSM en Windows Server...")
    for attempt in range(1, max_attempts + 1):
        res = ssm.describe_instance_information(
            Filters=[{'Key': 'InstanceIds', 'Values': [INSTANCE_ID]}]
        )
        instances = res.get('InstanceInformationList', [])
        if instances:
            inst = instances[0]
            print(f"✅ Agente SSM ONLINE!")
            print(f"   - Instancia: {inst['InstanceId']}")
            print(f"   - Ping Status: {inst['PingStatus']}")
            print(f"   - OS: {inst.get('PlatformName')} {inst.get('PlatformVersion')}")
            return True
        print(f"   [{attempt}/{max_attempts}] Esperando registro en SSM... ({delay}s)")
        time.sleep(delay)
    return False

def run_powershell(command: str, timeout_sec=120):
    print(f"\n[SSM] Enviando comando PowerShell...")
    response = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunPowerShellScript",
        Parameters={'commands': [command]},
        TimeoutSeconds=timeout_sec
    )
    cmd_id = response['Command']['CommandId']
    
    # Esperar ejecucion
    for _ in range(timeout_sec // 2):
        time.sleep(2)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=INSTANCE_ID)
        status = inv['Status']
        if status in ['Success', 'Failed', 'TimedOut', 'Cancelled']:
            print(f"[SSM] Estado de ejecucion: {status}")
            if inv.get('StandardOutputContent'):
                print("--- SALIDA ESTANDAR ---")
                print(inv['StandardOutputContent'])
            if inv.get('StandardErrorContent'):
                print("--- SALIDA ERROR ---")
                print(inv['StandardErrorContent'])
            return inv
    print("[SSM] Timeout esperando respuesta de comando.")
    return None

if __name__ == "__main__":
    if wait_for_ssm():
        print("\nProbando ejecucion básica...")
        run_powershell("hostname; whoami; Get-ComputerInfo | Select-Object WindowsProductName, CsTotalPhysicalMemory")
