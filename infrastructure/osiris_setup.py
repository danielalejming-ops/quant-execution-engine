import boto3
import time
import os

ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "YOUR_AWS_KEY_ID")
SECRET_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', 'mock_aws_secret_key')AWS_SECRET_ACCESS_KEY', 'mock_aws_secret_key')"
REGION = "us-east-1"

# Iniciar cliente
ec2 = boto3.client('ec2', region_name=REGION, aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY)
ec2_resource = boto3.resource('ec2', region_name=REGION, aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY)

try:
    # 1. Crear KeyPair
    print("Creando Llave SSL Osiris...")
    key_name = 'Llave-Osiris'
    try:
        ec2.delete_key_pair(KeyName=key_name)
    except:
        pass
    
    key_pair = ec2.create_key_pair(KeyName=key_name)
    with open("Llave-Osiris.pem", "w") as f:
        f.write(str(key_pair['KeyMaterial']))
    print("Llave-Osiris.pem creada correctamente.")
    
    # 2. Obtener Ubuntu 22.04 LTS AMI
    ami_response = ec2.describe_images(
        Filters=[
            {'Name': 'name', 'Values': ['ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*']},
            {'Name': 'state', 'Values': ['available']},
            {'Name': 'architecture', 'Values': ['x86_64']}
        ],
        Owners=['099720109477'] # Canonical owner ID
    )
    # Sort by creation date
    amis = sorted(ami_response['Images'], key=lambda x: x['CreationDate'], reverse=True)
    ami_id = amis[0]['ImageId']
    print(f"Mejor AMI Ubuntu encontrada: {ami_id}")
    
    # 3. Crear Security Group para puertos
    security_group_name = 'Osiris-Security-Group'
    try:
        ec2.delete_security_group(GroupName=security_group_name)
    except:
        pass
        
    sg_response = ec2.create_security_group(GroupName=security_group_name, Description='Permitir SSH y Web')
    sg_id = sg_response['GroupId']
    
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {'IpProtocol': 'tcp', 'FromPort': 22, 'ToPort': 22, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
            {'IpProtocol': 'tcp', 'FromPort': 8502, 'ToPort': 8502, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}
        ]
    )
    print("Security Group (Firewall) Osiris Creado y Abierto.")
    
    # 4. Iniciar Instancia EC2 T3.Micro (buena CPU/Red)
    instances = ec2_resource.create_instances(
        ImageId=ami_id,
        MinCount=1,
        MaxCount=1,
        InstanceType='t3.micro',
        KeyName=key_name,
        SecurityGroupIds=[sg_id],
        BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 20}}]
    )
    
    inst = instances[0]
    print("Esperando la instanciación completa en us-east-1...")
    inst.wait_until_running()
    inst.reload()
    
    print(f"\n==============================================")
    print(f"OSIRIS DEPLOYED NATIVELY IN AWS (us-east-1)!")
    print(f"Instance ID: {inst.id}")
    print(f"Public IP: {inst.public_ip_address}")
    print(f"==============================================")
    
except Exception as e:
    print(f"Error fatal: {e}")
