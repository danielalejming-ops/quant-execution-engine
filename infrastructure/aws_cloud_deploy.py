import os
import boto3
import time

ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "YOUR_AWS_KEY_ID")
SECRET_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', 'mock_aws_secret_key')AWS_SECRET_ACCESS_KEY', 'mock_aws_secret_key')"
REGION = "ap-northeast-1"

# Iniciar cliente
ec2 = boto3.client('ec2', region_name=REGION, aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY)
ec2_resource = boto3.resource('ec2', region_name=REGION, aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY)

try:
    # 1. Crear KeyPair
    # 1. Crear KeyPair
    print("1. Creando Llave SSL Estricta para Quant (Llave-Quant-Cloud-V4)...")
    key_name = 'Llave-Quant-Cloud-V4'
    try:
        ec2.delete_key_pair(KeyName=key_name)
    except:
        pass
    
    key_pair = ec2.create_key_pair(KeyName=key_name)
    with open("Llave-Quant-Cloud-V4.pem", "w") as f:
        f.write(str(key_pair['KeyMaterial']))
    print("   -> Llave-Quant-Cloud-V4.pem creada y descargada localmente.")
    
    # 2. Obtener Ubuntu 22.04 LTS AMI
    print("2. Localizando el Sistema Operativo (Ubuntu 22.04 LTS) más optimizado...")
    ami_response = ec2.describe_images(
        Filters=[
            {'Name': 'name', 'Values': ['ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*']},
            {'Name': 'state', 'Values': ['available']},
            {'Name': 'architecture', 'Values': ['x86_64']} # Standard intel/amd architecture compatible with our docker builds
        ],
        Owners=['099720109477'] # Canonical (Ubuntu)
    )
    amis = sorted(ami_response['Images'], key=lambda x: x['CreationDate'], reverse=True)
    ami_id = amis[0]['ImageId']
    print(f"   -> AMI seleccionada: {ami_id}")
    
    # 3. Crear Security Group para puertos
    print("3. Construyendo Firewall Institucional (Quant-SG-V2)...")
    security_group_name = 'Quant-Security-Group-V2'
    try:
        ec2.delete_security_group(GroupName=security_group_name)
    except:
        pass
        
    sg_response = ec2.create_security_group(GroupName=security_group_name, Description='Permitir SSH y Dashboard Web 8501')
    sg_id = sg_response['GroupId']
    
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {'IpProtocol': 'tcp', 'FromPort': 22, 'ToPort': 22, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
            {'IpProtocol': 'tcp', 'FromPort': 8501, 'ToPort': 8501, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}
        ]
    )
    print("   -> Puertos Cuánticos Tácticos [22, 8501] expuestos.")
    
    # 4. Iniciar Instancia EC2 T2.Micro (Capa Gratuita de AWS)
    print("4. Requisando hardware gratuito en el DataCenter de AWS (t2.micro)...")
    instances = ec2_resource.create_instances(
        ImageId=ami_id,
        MinCount=1,
        MaxCount=1,
        InstanceType='t2.micro', 
        KeyName=key_name,
        SecurityGroupIds=[sg_id],
        BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 15}}] # 15GB SSD
    )
    
    inst = instances[0]
    print(f"   -> Server aprovisionado con ID temporal: {inst.id}")
    print("   -> Esperando electricidad y arranque base... (Tardará 20-40 segundos)")
    
    # Esperar hasta que esté running para obtener IP Pública
    inst.wait_until_running()
    inst.reload() # Refrescar métricas dinámicas
    
    print(f"\n==============================================")
    print(f"🚀 SATELLITE NODE DEPLOYED NATALMENTE EN AWS (us-east-1)!")
    print(f"🚀 Instancia Activa. ID: {inst.id}")
    print(f"🚀 IP PÚBLICA DEL CEREBRO EN LA NUBE: {inst.public_ip_address}")
    print(f"==============================================\n")
    
except Exception as e:
    print(f"\n❌ Error fatal ensamblando infraestructura: {e}")
