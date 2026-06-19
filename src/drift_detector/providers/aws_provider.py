"""AWS cloud provider adapter for fetching actual resource state."""

from __future__ import annotations

import logging
from typing import Any

from ..models import ResourceMetadata

logger = logging.getLogger(__name__)


class AWSProvider:
    """AWS provider implementation using boto3.

    Fetches actual resource state from AWS APIs and normalizes it
    into ResourceMetadata for drift comparison.
    """

    def __init__(self, region: str = "us-east-1", profile: str | None = None):
        self.region = region
        self.profile = profile
        self._session = None
        self._clients: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "aws"

    @property
    def supported_resource_types(self) -> list[str]:
        return [
            "aws_instance",
            "aws_s3_bucket",
            "aws_security_group",
            "aws_vpc",
            "aws_subnet",
            "aws_lambda_function",
            "aws_iam_role",
            "aws_iam_policy",
            "aws_db_instance",
            "aws_dynamodb_table",
            "aws_ecs_cluster",
            "aws_ecs_service",
            "aws_lb",
            "aws_lb_target_group",
            "aws_route53_zone",
            "aws_cloudwatch_log_group",
            "aws_sns_topic",
            "aws_sqs_queue",
            "aws_elasticache_cluster",
            "aws_eks_cluster",
        ]

    def _get_session(self):
        """Lazy-initialize boto3 session."""
        if self._session is None:
            import boto3

            if self.profile:
                self._session = boto3.Session(
                    profile_name=self.profile, region_name=self.region
                )
            else:
                self._session = boto3.Session(region_name=self.region)
        return self._session

    def _get_client(self, service: str):
        """Get or create a boto3 client for the given service."""
        if service not in self._clients:
            session = self._get_session()
            self._clients[service] = session.client(service)
        return self._clients[service]

    def validate_credentials(self) -> bool:
        """Validate AWS credentials by calling STS GetCallerIdentity."""
        try:
            sts = self._get_client("sts")
            sts.get_caller_identity()
            return True
        except Exception as e:
            logger.error(f"AWS credential validation failed: {e}")
            return False

    def supports_resource(self, resource_type: str) -> bool:
        """Check if this provider handles the resource type."""
        return resource_type in self.supported_resource_types

    def get_resource(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch actual resource state from AWS."""
        handler_map = {
            "aws_instance": self._get_ec2_instance,
            "aws_s3_bucket": self._get_s3_bucket,
            "aws_security_group": self._get_security_group,
            "aws_vpc": self._get_vpc,
            "aws_subnet": self._get_subnet,
            "aws_lambda_function": self._get_lambda_function,
            "aws_iam_role": self._get_iam_role,
            "aws_db_instance": self._get_rds_instance,
            "aws_dynamodb_table": self._get_dynamodb_table,
            "aws_eks_cluster": self._get_eks_cluster,
            "aws_sns_topic": self._get_sns_topic,
            "aws_sqs_queue": self._get_sqs_queue,
            "aws_cloudwatch_log_group": self._get_cloudwatch_log_group,
        }

        handler = handler_map.get(resource.resource_type)
        if handler is None:
            logger.debug(f"No handler for resource type: {resource.resource_type}")
            return None

        try:
            return handler(resource)
        except Exception as e:
            error_type = type(e).__name__
            if "NotFound" in error_type or "NoSuch" in error_type or "404" in str(e):
                logger.info(f"Resource not found in AWS: {resource.get_key()}")
                return None
            logger.error(f"Error fetching {resource.get_key()}: {e}")
            raise

    def _get_ec2_instance(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch EC2 instance details."""
        ec2 = self._get_client("ec2")
        instance_id = resource.attributes.get("id", resource.resource_id)

        try:
            response = ec2.describe_instances(InstanceIds=[instance_id])
        except ec2.exceptions.ClientError as e:
            if "InvalidInstanceID.NotFound" in str(e):
                return None
            raise

        reservations = response.get("Reservations", [])
        if not reservations or not reservations[0].get("Instances"):
            return None

        instance = reservations[0]["Instances"][0]

        # Check if terminated
        state = instance.get("State", {}).get("Name", "")
        if state == "terminated":
            return None

        tags = self._aws_tags_to_dict(instance.get("Tags", []))

        # Get IAM instance profile
        iam_profile = instance.get("IamInstanceProfile", {})
        iam_instance_profile = iam_profile.get("Arn", "") if iam_profile else ""

        # Get root block device details
        root_device_name = instance.get("RootDeviceName", "")
        root_volume_size = None
        root_volume_type = None
        root_volume_encrypted = False
        for bdm in instance.get("BlockDeviceMappings", []):
            if bdm.get("DeviceName") == root_device_name:
                ebs = bdm.get("Ebs", {})
                volume_id = ebs.get("VolumeId")
                if volume_id:
                    try:
                        vol_resp = ec2.describe_volumes(VolumeIds=[volume_id])
                        volumes = vol_resp.get("Volumes", [])
                        if volumes:
                            root_volume_size = volumes[0].get("Size")
                            root_volume_type = volumes[0].get("VolumeType")
                            root_volume_encrypted = volumes[0].get("Encrypted", False)
                    except Exception:
                        pass
                break

        # Count attached volumes (detect manually attached EBS)
        attached_volume_count = len(instance.get("BlockDeviceMappings", []))

        # Count network interfaces (detect manually attached ENIs)
        network_interface_count = len(instance.get("NetworkInterfaces", []))

        # Get user data (detect manual changes)
        user_data_present = False
        try:
            ud_resp = ec2.describe_instance_attribute(
                InstanceId=instance_id, Attribute="userData"
            )
            user_data_val = ud_resp.get("UserData", {}).get("Value")
            user_data_present = user_data_val is not None and user_data_val != ""
        except Exception:
            pass

        # Get source/dest check
        source_dest_check = instance.get("SourceDestCheck", True)

        attributes = {
            "instance_type": instance.get("InstanceType"),
            "ami": instance.get("ImageId"),
            "availability_zone": instance.get("Placement", {}).get("AvailabilityZone"),
            "vpc_id": instance.get("VpcId"),
            "subnet_id": instance.get("SubnetId"),
            "key_name": instance.get("KeyName"),
            "monitoring": instance.get("Monitoring", {}).get("State") == "enabled",
            "ebs_optimized": instance.get("EbsOptimized", False),
            "instance_state": state,
            "public_ip": instance.get("PublicIpAddress"),
            "private_ip": instance.get("PrivateIpAddress"),
            "security_groups": sorted(
                [sg["GroupId"] for sg in instance.get("SecurityGroups", [])]
            ),
            "iam_instance_profile": iam_instance_profile,
            "root_volume_size": root_volume_size,
            "root_volume_type": root_volume_type,
            "root_volume_encrypted": root_volume_encrypted,
            "attached_volume_count": attached_volume_count,
            "network_interface_count": network_interface_count,
            "user_data_present": user_data_present,
            "source_dest_check": source_dest_check,
        }

        return ResourceMetadata(
            resource_type="aws_instance",
            resource_id=instance_id,
            resource_name=tags.get("Name", ""),
            provider="aws",
            region=self.region,
            attributes=attributes,
            tags=tags,
            raw=instance,
        )

    def _get_s3_bucket(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch S3 bucket details."""
        s3 = self._get_client("s3")
        bucket_name = resource.attributes.get("bucket", resource.resource_id)

        try:
            s3.head_bucket(Bucket=bucket_name)
        except Exception as e:
            if "404" in str(e) or "NoSuchBucket" in str(e):
                return None
            raise

        attributes: dict[str, Any] = {"bucket": bucket_name}

        # Get versioning
        try:
            versioning = s3.get_bucket_versioning(Bucket=bucket_name)
            attributes["versioning_enabled"] = versioning.get("Status") == "Enabled"
        except Exception:
            attributes["versioning_enabled"] = False

        # Get tags
        tags: dict[str, str] = {}
        try:
            tag_response = s3.get_bucket_tagging(Bucket=bucket_name)
            tags = self._aws_tags_to_dict(tag_response.get("TagSet", []))
        except Exception:
            pass

        # Get encryption
        try:
            encryption = s3.get_bucket_encryption(Bucket=bucket_name)
            rules = encryption.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
            if rules:
                sse = rules[0].get("ApplyServerSideEncryptionByDefault", {})
                attributes["sse_algorithm"] = sse.get("SSEAlgorithm")
        except Exception:
            attributes["sse_algorithm"] = None

        # Get ACL
        try:
            acl = s3.get_bucket_acl(Bucket=bucket_name)
            grants = acl.get("Grants", [])
            # Determine effective ACL
            public_read = any(
                g.get("Grantee", {}).get("URI") == "http://acs.amazonaws.com/groups/global/AllUsers"
                and g.get("Permission") in ("READ", "FULL_CONTROL")
                for g in grants
            )
            attributes["acl_public_read"] = public_read
        except Exception:
            pass

        # Get bucket policy (check if one exists)
        try:
            policy_response = s3.get_bucket_policy(Bucket=bucket_name)
            attributes["has_policy"] = True
            attributes["policy"] = policy_response.get("Policy", "")
        except Exception as e:
            if "NoSuchBucketPolicy" in str(e):
                attributes["has_policy"] = False
                attributes["policy"] = ""
            else:
                pass

        # Get logging configuration
        try:
            logging_resp = s3.get_bucket_logging(Bucket=bucket_name)
            log_config = logging_resp.get("LoggingEnabled")
            attributes["logging_enabled"] = log_config is not None
            if log_config:
                attributes["logging_target_bucket"] = log_config.get("TargetBucket", "")
        except Exception:
            pass

        # Get lifecycle rules
        try:
            lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=bucket_name)
            rules = lifecycle.get("Rules", [])
            attributes["lifecycle_rules_count"] = len(rules)
        except Exception as e:
            if "NoSuchLifecycleConfiguration" in str(e):
                attributes["lifecycle_rules_count"] = 0

        # Get object count (detect unmanaged objects added manually)
        try:
            object_count = 0
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket_name):
                object_count += page.get("KeyCount", 0)
            attributes["object_count"] = object_count
        except Exception:
            pass

        return ResourceMetadata(
            resource_type="aws_s3_bucket",
            resource_id=bucket_name,
            resource_name=bucket_name,
            provider="aws",
            region=self.region,
            attributes=attributes,
            tags=tags,
            raw=attributes,
        )

    def _get_security_group(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch security group details."""
        ec2 = self._get_client("ec2")
        sg_id = resource.attributes.get("id", resource.resource_id)

        try:
            response = ec2.describe_security_groups(GroupIds=[sg_id])
        except Exception as e:
            if "InvalidGroup.NotFound" in str(e):
                return None
            raise

        groups = response.get("SecurityGroups", [])
        if not groups:
            return None

        sg = groups[0]
        tags = self._aws_tags_to_dict(sg.get("Tags", []))

        # Build detailed ingress rules for comparison
        ingress_rules = []
        for rule in sg.get("IpPermissions", []):
            for ip_range in rule.get("IpRanges", []):
                ingress_rules.append({
                    "protocol": rule.get("IpProtocol", "-1"),
                    "from_port": rule.get("FromPort", 0),
                    "to_port": rule.get("ToPort", 0),
                    "cidr": ip_range.get("CidrIp", ""),
                })
            for ipv6_range in rule.get("Ipv6Ranges", []):
                ingress_rules.append({
                    "protocol": rule.get("IpProtocol", "-1"),
                    "from_port": rule.get("FromPort", 0),
                    "to_port": rule.get("ToPort", 0),
                    "cidr_ipv6": ipv6_range.get("CidrIpv6", ""),
                })
            for sg_ref in rule.get("UserIdGroupPairs", []):
                ingress_rules.append({
                    "protocol": rule.get("IpProtocol", "-1"),
                    "from_port": rule.get("FromPort", 0),
                    "to_port": rule.get("ToPort", 0),
                    "source_sg": sg_ref.get("GroupId", ""),
                })

        # Build detailed egress rules
        egress_rules = []
        for rule in sg.get("IpPermissionsEgress", []):
            for ip_range in rule.get("IpRanges", []):
                egress_rules.append({
                    "protocol": rule.get("IpProtocol", "-1"),
                    "from_port": rule.get("FromPort", 0),
                    "to_port": rule.get("ToPort", 0),
                    "cidr": ip_range.get("CidrIp", ""),
                })
            for ipv6_range in rule.get("Ipv6Ranges", []):
                egress_rules.append({
                    "protocol": rule.get("IpProtocol", "-1"),
                    "from_port": rule.get("FromPort", 0),
                    "to_port": rule.get("ToPort", 0),
                    "cidr_ipv6": ipv6_range.get("CidrIpv6", ""),
                })
            for sg_ref in rule.get("UserIdGroupPairs", []):
                egress_rules.append({
                    "protocol": rule.get("IpProtocol", "-1"),
                    "from_port": rule.get("FromPort", 0),
                    "to_port": rule.get("ToPort", 0),
                    "source_sg": sg_ref.get("GroupId", ""),
                })

        attributes = {
            "name": sg.get("GroupName"),
            "description": sg.get("Description"),
            "vpc_id": sg.get("VpcId"),
            "ingress_rules_count": len(sg.get("IpPermissions", [])),
            "egress_rules_count": len(sg.get("IpPermissionsEgress", [])),
            "ingress_rules": sorted(ingress_rules, key=lambda x: str(x)),
            "egress_rules": sorted(egress_rules, key=lambda x: str(x)),
        }

        return ResourceMetadata(
            resource_type="aws_security_group",
            resource_id=sg_id,
            resource_name=sg.get("GroupName", ""),
            provider="aws",
            region=self.region,
            attributes=attributes,
            tags=tags,
            raw=sg,
        )

    def _get_vpc(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch VPC details."""
        ec2 = self._get_client("ec2")
        vpc_id = resource.attributes.get("id", resource.resource_id)

        try:
            response = ec2.describe_vpcs(VpcIds=[vpc_id])
        except Exception as e:
            if "InvalidVpcID.NotFound" in str(e):
                return None
            raise

        vpcs = response.get("Vpcs", [])
        if not vpcs:
            return None

        vpc = vpcs[0]
        tags = self._aws_tags_to_dict(vpc.get("Tags", []))

        # Get DNS attributes
        enable_dns_hostnames = False
        try:
            dns_hostnames = ec2.describe_vpc_attribute(
                VpcId=vpc_id, Attribute="enableDnsHostnames"
            )
            enable_dns_hostnames = dns_hostnames.get("EnableDnsHostnames", {}).get("Value", False)
        except Exception:
            pass

        enable_dns_support = True
        try:
            dns_support = ec2.describe_vpc_attribute(
                VpcId=vpc_id, Attribute="enableDnsSupport"
            )
            enable_dns_support = dns_support.get("EnableDnsSupport", {}).get("Value", True)
        except Exception:
            pass

        # Count subnets in this VPC (detect manually added subnets)
        subnet_count = 0
        try:
            subnet_resp = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
            subnet_count = len(subnet_resp.get("Subnets", []))
        except Exception:
            pass

        # Count route tables (detect manually added)
        route_table_count = 0
        try:
            rt_resp = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
            route_table_count = len(rt_resp.get("RouteTables", []))
        except Exception:
            pass

        # Count internet gateways (detect manually attached)
        igw_count = 0
        try:
            igw_resp = ec2.describe_internet_gateways(
                Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
            )
            igw_count = len(igw_resp.get("InternetGateways", []))
        except Exception:
            pass

        # Count NAT gateways
        nat_gw_count = 0
        try:
            nat_resp = ec2.describe_nat_gateways(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )
            nat_gw_count = len([
                n for n in nat_resp.get("NatGateways", [])
                if n.get("State") != "deleted"
            ])
        except Exception:
            pass

        attributes = {
            "cidr_block": vpc.get("CidrBlock"),
            "state": vpc.get("State"),
            "enable_dns_hostnames": enable_dns_hostnames,
            "enable_dns_support": enable_dns_support,
            "instance_tenancy": vpc.get("InstanceTenancy"),
            "subnet_count": subnet_count,
            "route_table_count": route_table_count,
            "internet_gateway_count": igw_count,
            "nat_gateway_count": nat_gw_count,
        }

        return ResourceMetadata(
            resource_type="aws_vpc",
            resource_id=vpc_id,
            resource_name=tags.get("Name", ""),
            provider="aws",
            region=self.region,
            attributes=attributes,
            tags=tags,
            raw=vpc,
        )

    def _get_subnet(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch subnet details."""
        ec2 = self._get_client("ec2")
        subnet_id = resource.attributes.get("id", resource.resource_id)

        try:
            response = ec2.describe_subnets(SubnetIds=[subnet_id])
        except Exception as e:
            if "InvalidSubnetID.NotFound" in str(e):
                return None
            raise

        subnets = response.get("Subnets", [])
        if not subnets:
            return None

        subnet = subnets[0]
        tags = self._aws_tags_to_dict(subnet.get("Tags", []))

        # Check route table association
        route_table_id = ""
        try:
            rt_resp = ec2.describe_route_tables(
                Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
            )
            rts = rt_resp.get("RouteTables", [])
            if rts:
                route_table_id = rts[0].get("RouteTableId", "")
        except Exception:
            pass

        # Check network ACL association
        nacl_id = ""
        try:
            nacl_resp = ec2.describe_network_acls(
                Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
            )
            nacls = nacl_resp.get("NetworkAcls", [])
            if nacls:
                nacl_id = nacls[0].get("NetworkAclId", "")
        except Exception:
            pass

        attributes = {
            "cidr_block": subnet.get("CidrBlock"),
            "vpc_id": subnet.get("VpcId"),
            "availability_zone": subnet.get("AvailabilityZone"),
            "map_public_ip_on_launch": subnet.get("MapPublicIpOnLaunch", False),
            "assign_ipv6_address_on_creation": subnet.get("AssignIpv6AddressOnCreation", False),
            "route_table_id": route_table_id,
            "network_acl_id": nacl_id,
        }

        return ResourceMetadata(
            resource_type="aws_subnet",
            resource_id=subnet_id,
            resource_name=tags.get("Name", ""),
            provider="aws",
            region=self.region,
            attributes=attributes,
            tags=tags,
            raw=subnet,
        )

    def _get_lambda_function(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch Lambda function details."""
        lam = self._get_client("lambda")
        function_name = resource.attributes.get("function_name", resource.resource_name)

        try:
            response = lam.get_function(FunctionName=function_name)
        except Exception as e:
            if "ResourceNotFoundException" in str(e):
                return None
            raise

        config = response.get("Configuration", {})
        tags = response.get("Tags", {})

        # Get environment variables
        env_vars = config.get("Environment", {}).get("Variables", {})

        # Get VPC config
        vpc_config = config.get("VpcConfig", {})
        vpc_subnet_ids = sorted(vpc_config.get("SubnetIds", []))
        vpc_sg_ids = sorted(vpc_config.get("SecurityGroupIds", []))

        # Get concurrency settings
        reserved_concurrency = None
        try:
            conc_resp = lam.get_function_concurrency(FunctionName=function_name)
            reserved_concurrency = conc_resp.get("ReservedConcurrentExecutions")
        except Exception:
            pass

        # Get event source mappings count (detect manually added triggers)
        event_source_count = 0
        try:
            esm_resp = lam.list_event_source_mappings(FunctionName=function_name)
            event_source_count = len(esm_resp.get("EventSourceMappings", []))
        except Exception:
            pass

        # Get layers
        layers = [layer.get("Arn", "") for layer in config.get("Layers", [])]

        attributes = {
            "function_name": config.get("FunctionName"),
            "runtime": config.get("Runtime"),
            "handler": config.get("Handler"),
            "memory_size": config.get("MemorySize"),
            "timeout": config.get("Timeout"),
            "role": config.get("Role"),
            "description": config.get("Description", ""),
            "environment_variables": env_vars,
            "vpc_subnet_ids": vpc_subnet_ids,
            "vpc_security_group_ids": vpc_sg_ids,
            "reserved_concurrency": reserved_concurrency,
            "event_source_mapping_count": event_source_count,
            "layers": sorted(layers),
            "package_type": config.get("PackageType", "Zip"),
            "architectures": config.get("Architectures", ["x86_64"]),
        }

        return ResourceMetadata(
            resource_type="aws_lambda_function",
            resource_id=config.get("FunctionArn", function_name),
            resource_name=function_name,
            provider="aws",
            region=self.region,
            attributes=attributes,
            tags=tags,
            raw=config,
        )

    def _get_iam_role(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch IAM role details."""
        iam = self._get_client("iam")
        role_name = resource.attributes.get("name", resource.resource_name)

        try:
            response = iam.get_role(RoleName=role_name)
        except Exception as e:
            if "NoSuchEntity" in str(e):
                return None
            raise

        role = response.get("Role", {})
        tags = self._aws_tags_to_dict(role.get("Tags", []))

        # Get attached managed policies (detect manually attached policies)
        attached_policies = []
        try:
            pol_resp = iam.list_attached_role_policies(RoleName=role_name)
            attached_policies = sorted(
                [p.get("PolicyArn", "") for p in pol_resp.get("AttachedPolicies", [])]
            )
        except Exception:
            pass

        # Get inline policies (detect manually added inline policies)
        inline_policies = []
        try:
            inline_resp = iam.list_role_policies(RoleName=role_name)
            inline_policies = sorted(inline_resp.get("PolicyNames", []))
        except Exception:
            pass

        # Get assume role policy document
        assume_role_policy = role.get("AssumeRolePolicyDocument", {})

        attributes = {
            "name": role.get("RoleName"),
            "path": role.get("Path"),
            "description": role.get("Description", ""),
            "max_session_duration": role.get("MaxSessionDuration"),
            "attached_policy_arns": attached_policies,
            "inline_policy_names": inline_policies,
            "attached_policy_count": len(attached_policies),
            "inline_policy_count": len(inline_policies),
            "assume_role_policy": assume_role_policy,
        }

        return ResourceMetadata(
            resource_type="aws_iam_role",
            resource_id=role.get("Arn", role_name),
            resource_name=role_name,
            provider="aws",
            region="global",
            attributes=attributes,
            tags=tags,
            raw=role,
        )

    def _get_rds_instance(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch RDS instance details."""
        rds = self._get_client("rds")
        db_id = resource.attributes.get("identifier", resource.resource_name)

        try:
            response = rds.describe_db_instances(DBInstanceIdentifier=db_id)
        except Exception as e:
            if "DBInstanceNotFound" in str(e):
                return None
            raise

        instances = response.get("DBInstances", [])
        if not instances:
            return None

        db = instances[0]
        tags_response = rds.list_tags_for_resource(ResourceName=db.get("DBInstanceArn", ""))
        tags = self._aws_tags_to_dict(tags_response.get("TagList", []))

        # Get security groups attached
        vpc_sgs = sorted([
            sg.get("VpcSecurityGroupId", "")
            for sg in db.get("VpcSecurityGroups", [])
            if sg.get("Status") == "active"
        ])

        # Get parameter group
        param_groups = db.get("DBParameterGroups", [])
        parameter_group = param_groups[0].get("DBParameterGroupName", "") if param_groups else ""

        # Get subnet group
        subnet_group = db.get("DBSubnetGroup", {}).get("DBSubnetGroupName", "")

        # Check automated backups / maintenance window
        backup_retention = db.get("BackupRetentionPeriod", 0)
        maintenance_window = db.get("PreferredMaintenanceWindow", "")
        backup_window = db.get("PreferredBackupWindow", "")

        # Check deletion protection
        deletion_protection = db.get("DeletionProtection", False)

        # Check auto minor version upgrade
        auto_minor_version_upgrade = db.get("AutoMinorVersionUpgrade", False)

        attributes = {
            "identifier": db.get("DBInstanceIdentifier"),
            "instance_class": db.get("DBInstanceClass"),
            "engine": db.get("Engine"),
            "engine_version": db.get("EngineVersion"),
            "allocated_storage": db.get("AllocatedStorage"),
            "multi_az": db.get("MultiAZ", False),
            "storage_encrypted": db.get("StorageEncrypted", False),
            "publicly_accessible": db.get("PubliclyAccessible", False),
            "status": db.get("DBInstanceStatus"),
            "vpc_security_group_ids": vpc_sgs,
            "parameter_group_name": parameter_group,
            "db_subnet_group_name": subnet_group,
            "backup_retention_period": backup_retention,
            "maintenance_window": maintenance_window,
            "backup_window": backup_window,
            "deletion_protection": deletion_protection,
            "auto_minor_version_upgrade": auto_minor_version_upgrade,
            "storage_type": db.get("StorageType", ""),
            "iops": db.get("Iops"),
            "port": db.get("Endpoint", {}).get("Port"),
        }

        return ResourceMetadata(
            resource_type="aws_db_instance",
            resource_id=db.get("DBInstanceArn", db_id),
            resource_name=db_id,
            provider="aws",
            region=self.region,
            attributes=attributes,
            tags=tags,
            raw=db,
        )

    def _get_dynamodb_table(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch DynamoDB table details."""
        dynamodb = self._get_client("dynamodb")
        table_name = resource.attributes.get("name", resource.resource_name)

        try:
            response = dynamodb.describe_table(TableName=table_name)
        except Exception as e:
            if "ResourceNotFoundException" in str(e):
                return None
            raise

        table = response.get("Table", {})

        # Get tags
        tags: dict[str, str] = {}
        try:
            tags_response = dynamodb.list_tags_of_resource(ResourceArn=table.get("TableArn", ""))
            tags = self._aws_tags_to_dict(tags_response.get("Tags", []))
        except Exception:
            pass

        # Get key schema
        key_schema = table.get("KeySchema", [])
        hash_key = ""
        range_key = ""
        for key in key_schema:
            if key.get("KeyType") == "HASH":
                hash_key = key.get("AttributeName", "")
            elif key.get("KeyType") == "RANGE":
                range_key = key.get("AttributeName", "")

        # Get GSI count (detect manually added indexes)
        gsi_count = len(table.get("GlobalSecondaryIndexes", []))
        lsi_count = len(table.get("LocalSecondaryIndexes", []))

        # Get stream specification
        stream_spec = table.get("StreamSpecification", {})
        stream_enabled = stream_spec.get("StreamEnabled", False)
        stream_view_type = stream_spec.get("StreamViewType", "")

        # Get TTL
        ttl_enabled = False
        ttl_attribute = ""
        try:
            ttl_resp = dynamodb.describe_time_to_live(TableName=table_name)
            ttl_desc = ttl_resp.get("TimeToLiveDescription", {})
            ttl_enabled = ttl_desc.get("TimeToLiveStatus") == "ENABLED"
            ttl_attribute = ttl_desc.get("AttributeName", "")
        except Exception:
            pass

        # Get point-in-time recovery
        pitr_enabled = False
        try:
            pitr_resp = dynamodb.describe_continuous_backups(TableName=table_name)
            pitr_desc = pitr_resp.get("ContinuousBackupsDescription", {})
            pitr_status = pitr_desc.get("PointInTimeRecoveryDescription", {}).get(
                "PointInTimeRecoveryStatus", ""
            )
            pitr_enabled = pitr_status == "ENABLED"
        except Exception:
            pass

        # Get encryption
        sse_description = table.get("SSEDescription", {})
        sse_enabled = sse_description.get("Status") == "ENABLED"
        sse_type = sse_description.get("SSEType", "")

        attributes = {
            "name": table.get("TableName"),
            "billing_mode": table.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED"),
            "table_status": table.get("TableStatus"),
            "item_count": table.get("ItemCount"),
            "table_size_bytes": table.get("TableSizeBytes"),
            "hash_key": hash_key,
            "range_key": range_key,
            "gsi_count": gsi_count,
            "lsi_count": lsi_count,
            "stream_enabled": stream_enabled,
            "stream_view_type": stream_view_type,
            "ttl_enabled": ttl_enabled,
            "ttl_attribute_name": ttl_attribute,
            "point_in_time_recovery_enabled": pitr_enabled,
            "server_side_encryption_enabled": sse_enabled,
            "server_side_encryption_type": sse_type,
        }

        return ResourceMetadata(
            resource_type="aws_dynamodb_table",
            resource_id=table.get("TableArn", table_name),
            resource_name=table_name,
            provider="aws",
            region=self.region,
            attributes=attributes,
            tags=tags,
            raw=table,
        )

    def _get_eks_cluster(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch EKS cluster details."""
        eks = self._get_client("eks")
        cluster_name = resource.attributes.get("name", resource.resource_name)

        try:
            response = eks.describe_cluster(name=cluster_name)
        except Exception as e:
            if "ResourceNotFoundException" in str(e):
                return None
            raise

        cluster = response.get("cluster", {})
        tags = cluster.get("tags", {})

        # Get VPC config
        vpc_config = cluster.get("resourcesVpcConfig", {})
        subnet_ids = sorted(vpc_config.get("subnetIds", []))
        security_group_ids = sorted(vpc_config.get("securityGroupIds", []))
        endpoint_public = vpc_config.get("endpointPublicAccess", True)
        endpoint_private = vpc_config.get("endpointPrivateAccess", False)

        # Get logging config
        logging_config = cluster.get("logging", {}).get("clusterLogging", [])
        enabled_log_types = []
        for log_group in logging_config:
            if log_group.get("enabled"):
                enabled_log_types.extend(log_group.get("types", []))
        enabled_log_types = sorted(enabled_log_types)

        # Get encryption config
        encryption_config = cluster.get("encryptionConfig", [])
        encryption_enabled = len(encryption_config) > 0

        # Get node groups count (detect manually added node groups)
        nodegroup_count = 0
        try:
            ng_resp = eks.list_nodegroups(clusterName=cluster_name)
            nodegroup_count = len(ng_resp.get("nodegroups", []))
        except Exception:
            pass

        # Get addons (detect manually installed addons)
        addon_names = []
        try:
            addon_resp = eks.list_addons(clusterName=cluster_name)
            addon_names = sorted(addon_resp.get("addons", []))
        except Exception:
            pass

        attributes = {
            "name": cluster.get("name"),
            "version": cluster.get("version"),
            "status": cluster.get("status"),
            "platform_version": cluster.get("platformVersion"),
            "role_arn": cluster.get("roleArn"),
            "subnet_ids": subnet_ids,
            "security_group_ids": security_group_ids,
            "endpoint_public_access": endpoint_public,
            "endpoint_private_access": endpoint_private,
            "enabled_log_types": enabled_log_types,
            "encryption_enabled": encryption_enabled,
            "nodegroup_count": nodegroup_count,
            "addon_names": addon_names,
        }

        return ResourceMetadata(
            resource_type="aws_eks_cluster",
            resource_id=cluster.get("arn", cluster_name),
            resource_name=cluster_name,
            provider="aws",
            region=self.region,
            attributes=attributes,
            tags=tags,
            raw=cluster,
        )

    def _get_sns_topic(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch SNS topic details."""
        sns = self._get_client("sns")
        topic_arn = resource.attributes.get("arn", resource.resource_id)

        try:
            response = sns.get_topic_attributes(TopicArn=topic_arn)
        except Exception as e:
            if "NotFound" in str(e):
                return None
            raise

        attrs = response.get("Attributes", {})
        tags_response = sns.list_tags_for_resource(ResourceArn=topic_arn)
        tags = self._aws_tags_to_dict(tags_response.get("Tags", []))

        # Get subscription count (detect manually added subscriptions)
        subscription_count = 0
        try:
            sub_resp = sns.list_subscriptions_by_topic(TopicArn=topic_arn)
            subscription_count = len(sub_resp.get("Subscriptions", []))
        except Exception:
            pass

        # Get delivery policy and access policy
        has_access_policy = bool(attrs.get("Policy", ""))
        delivery_policy = attrs.get("DeliveryPolicy", "")

        attributes = {
            "display_name": attrs.get("DisplayName", ""),
            "kms_master_key_id": attrs.get("KmsMasterKeyId", ""),
            "fifo_topic": attrs.get("FifoTopic", "false") == "true",
            "content_based_deduplication": attrs.get("ContentBasedDeduplication", "false") == "true",
            "subscription_count": subscription_count,
            "has_access_policy": has_access_policy,
            "has_delivery_policy": bool(delivery_policy),
        }

        return ResourceMetadata(
            resource_type="aws_sns_topic",
            resource_id=topic_arn,
            resource_name=attrs.get("DisplayName", topic_arn.split(":")[-1]),
            provider="aws",
            region=self.region,
            attributes=attributes,
            tags=tags,
            raw=attrs,
        )

    def _get_sqs_queue(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch SQS queue details."""
        sqs = self._get_client("sqs")
        queue_url = resource.attributes.get("url", "")
        queue_name = resource.attributes.get("name", resource.resource_name)

        if not queue_url:
            try:
                url_response = sqs.get_queue_url(QueueName=queue_name)
                queue_url = url_response.get("QueueUrl", "")
            except Exception as e:
                if "NonExistentQueue" in str(e):
                    return None
                raise

        try:
            response = sqs.get_queue_attributes(
                QueueUrl=queue_url, AttributeNames=["All"]
            )
        except Exception as e:
            if "NonExistentQueue" in str(e):
                return None
            raise

        attrs = response.get("Attributes", {})
        tags_response = sqs.list_queue_tags(QueueUrl=queue_url)
        tags = tags_response.get("Tags", {})

        attributes = {
            "name": queue_name,
            "delay_seconds": int(attrs.get("DelaySeconds", 0)),
            "max_message_size": int(attrs.get("MaximumMessageSize", 262144)),
            "message_retention_seconds": int(attrs.get("MessageRetentionPeriod", 345600)),
            "visibility_timeout_seconds": int(attrs.get("VisibilityTimeout", 30)),
            "fifo_queue": attrs.get("FifoQueue", "false") == "true",
        }

        return ResourceMetadata(
            resource_type="aws_sqs_queue",
            resource_id=queue_url,
            resource_name=queue_name,
            provider="aws",
            region=self.region,
            attributes=attributes,
            tags=tags,
            raw=attrs,
        )

    def _get_cloudwatch_log_group(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch CloudWatch log group details."""
        logs = self._get_client("logs")
        log_group_name = resource.attributes.get("name", resource.resource_name)

        try:
            response = logs.describe_log_groups(logGroupNamePrefix=log_group_name)
        except Exception:
            return None

        groups = response.get("logGroups", [])
        # Find exact match
        matching = [g for g in groups if g.get("logGroupName") == log_group_name]
        if not matching:
            return None

        lg = matching[0]

        # Get tags
        tags: dict[str, str] = {}
        try:
            tags_response = logs.list_tags_log_group(logGroupName=log_group_name)
            tags = tags_response.get("tags", {})
        except Exception:
            pass

        attributes = {
            "name": lg.get("logGroupName"),
            "retention_in_days": lg.get("retentionInDays", 0),
            "kms_key_id": lg.get("kmsKeyId", ""),
        }

        return ResourceMetadata(
            resource_type="aws_cloudwatch_log_group",
            resource_id=lg.get("arn", log_group_name),
            resource_name=log_group_name,
            provider="aws",
            region=self.region,
            attributes=attributes,
            tags=tags,
            raw=lg,
        )

    @staticmethod
    def _aws_tags_to_dict(tags_list: list[dict[str, str]]) -> dict[str, str]:
        """Convert AWS tag list format [{Key: k, Value: v}] to dict."""
        result = {}
        for tag in tags_list:
            key = tag.get("Key") or tag.get("key", "")
            value = tag.get("Value") or tag.get("value", "")
            if key:
                result[key] = value
        return result
