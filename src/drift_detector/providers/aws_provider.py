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

        attributes = {
            "name": sg.get("GroupName"),
            "description": sg.get("Description"),
            "vpc_id": sg.get("VpcId"),
            "ingress_rules_count": len(sg.get("IpPermissions", [])),
            "egress_rules_count": len(sg.get("IpPermissionsEgress", [])),
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

        attributes = {
            "cidr_block": vpc.get("CidrBlock"),
            "state": vpc.get("State"),
            "enable_dns_hostnames": vpc.get("EnableDnsHostnames", False),
            "enable_dns_support": vpc.get("EnableDnsSupport", True),
            "instance_tenancy": vpc.get("InstanceTenancy"),
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

        attributes = {
            "cidr_block": subnet.get("CidrBlock"),
            "vpc_id": subnet.get("VpcId"),
            "availability_zone": subnet.get("AvailabilityZone"),
            "map_public_ip_on_launch": subnet.get("MapPublicIpOnLaunch", False),
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

        attributes = {
            "function_name": config.get("FunctionName"),
            "runtime": config.get("Runtime"),
            "handler": config.get("Handler"),
            "memory_size": config.get("MemorySize"),
            "timeout": config.get("Timeout"),
            "role": config.get("Role"),
            "last_modified": config.get("LastModified"),
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

        attributes = {
            "name": role.get("RoleName"),
            "path": role.get("Path"),
            "description": role.get("Description", ""),
            "max_session_duration": role.get("MaxSessionDuration"),
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

        attributes = {
            "name": table.get("TableName"),
            "billing_mode": table.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED"),
            "table_status": table.get("TableStatus"),
            "item_count": table.get("ItemCount"),
            "table_size_bytes": table.get("TableSizeBytes"),
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

        attributes = {
            "name": cluster.get("name"),
            "version": cluster.get("version"),
            "status": cluster.get("status"),
            "platform_version": cluster.get("platformVersion"),
            "role_arn": cluster.get("roleArn"),
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

        attributes = {
            "display_name": attrs.get("DisplayName", ""),
            "kms_master_key_id": attrs.get("KmsMasterKeyId", ""),
            "fifo_topic": attrs.get("FifoTopic", "false") == "true",
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
