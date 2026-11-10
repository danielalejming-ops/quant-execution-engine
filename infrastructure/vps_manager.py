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

def run_ps(cmd_list, timeout=35):
    ssm_timeout = max(30, int(timeout))
    cmd = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunPowerShellScript",
        Parameters={'commands': cmd_list},
        TimeoutSeconds=ssm_timeout
    )
    cmd_id = cmd['Command']['CommandId']
    start_time = time.time()
    while time.time() - start_time < timeout:
        time.sleep(2)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=INSTANCE_ID)
        status = inv['Status']
        if status in ['Success', 'Failed', 'TimedOut', 'Cancelled']:
            print(f"[{status}] (Elapsed: {time.time()-start_time:.1f}s)")
            stdout = inv.get('StandardOutputContent', '').strip()
            stderr = inv.get('StandardErrorContent', '').strip()
            if stdout:
                print("--- STDOUT ---")
                print(stdout)
            if stderr:
                print("--- STDERR ---")
                print(stderr)
            return status, stdout, stderr
    print("[TIMEOUT waiting for command]")
    return "Timeout", "", ""

if __name__ == '__main__':
    commands = [
        "Write-Host '=== PROCESSES ==='",
        "Get-Process terminal64, python, cmd -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, MainWindowTitle | Format-Table -AutoSize",
        "Write-Host '=== DAEMON LOG (LAST 25 LINES) ==='",
        "if (Test-Path 'C:\\VP1_Trader\\poc_gold_daemon.log') { Get-Content 'C:\\VP1_Trader\\poc_gold_daemon.log' -Tail 25 } else { Write-Host 'No daemon log' }",
        "Write-Host '=== DAEMON PID FILE ==='",
        "if (Test-Path 'C:\\VP1_Trader\\poc_gold_daemon.pid') { Get-Content 'C:\\VP1_Trader\\poc_gold_daemon.pid' } else { Write-Host 'No pid file' }"
    ]
    run_ps(commands)
