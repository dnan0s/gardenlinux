import pytest
import logging
import yaml

import glci.util

from typing import Iterator
from .sshclient import RemoteClient

from util import ctx
from dataclasses import dataclass
from _pytest.config.argparsing import Parser

from .aws import AWS
from .azure import AZURE

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

def pytest_addoption(parser: Parser):
    parser.addoption(
        "--iaas",
        action="store",
        help="Infrastructure the tests should run on",
    )
    parser.addoption(
        "--configfile",
        action="store",
        default="test_config.yaml",
        help="Test configuration file"
    )
    parser.addoption(
        "--pipeline",
        action='store_true',
        help="tests are run from a pipeline context and thus, certain pieces of information are retrieved differently"
    )
    parser.addoption(
        "--image",
        nargs="?",
        help="URI for the image to be tested (overwrites value in config.yaml)"
    )
#    parser.addoption(
#        "--debug",
#        action="store_true",
#        help="debug"
#    )


@pytest.fixture(scope="session")
def pipeline(pytestconfig):
    if pytestconfig.getoption('pipeline'):
        return True
    return False


@pytest.fixture(scope="session")
def iaas(pytestconfig):
    if pytestconfig.getoption('iaas'):
        return pytestconfig.getoption('iaas')
    pytest.exit("Need to specify which IaaS to test on.", 1)


@pytest.fixture(scope="session")
def s3_image_location(test_params):
    ''' 
    returns a S3Info object and gives access to the S3 bucket containing the build artifacts
    from the current pipeline run. Typically use to be uploaded to hyperscalers for testing.
    '''

    @dataclass
    class S3Info:
        bucket_name: str
        raw_image_key: str
        target_image_name: str

    cicd_cfg = glci.util.cicd_cfg(cfg_name=test_params.cicd_cfg_name)
    find_release = glci.util.preconfigured(
        func=glci.util.find_release,
        cicd_cfg=cicd_cfg,
    )

    release = find_release(
        release_identifier=glci.model.ReleaseIdentifier(
            build_committish=test_params.committish,
            version=test_params.version,
            gardenlinux_epoch=int(test_params.gardenlinux_epoch),
            architecture=glci.model.Architecture(test_params.architecture),
            platform=test_params.platform,
            modifiers=test_params.modifiers,
        ),
    )

    return S3Info(
        raw_image_key=release.path_by_suffix('rootfs.raw').s3_key,
        bucket_name=release.path_by_suffix('rootfs.raw').s3_bucket_name,
        target_image_name=f'integration-test-image-{test_params.committish}',
    )


@pytest.fixture(scope="session")
def imageurl(pipeline, testconfig, pytestconfig, request):
    if pipeline:
        s3_image_location = request.getfixturevalue('s3_image_location')
        return f's3://{s3_image_location.bucket_name}/{s3_image_location.raw_image_key}'
    elif pytestconfig.getoption('image'):
        return pytestconfig.getoption('image')
    else:
        if 'image' in testconfig:
            return testconfig['image']


@pytest.fixture(scope="session")
def testconfig(pipeline, iaas, pytestconfig):
    if not pipeline:
        configfile = pytestconfig.getoption("configfile")
        try:
            with open(configfile) as f:
                configoptions = yaml.load(f, Loader=yaml.FullLoader)
        except OSError as err:
            pytest.exit(err, 1)
        if iaas in configoptions:
            return configoptions[iaas]
        else:
            pytest.exit(f"Configuration section for {iaas} not found in {configfile}.", 1)
    else:
        if iaas == 'aws':
            ssh_config = {
                'user': 'admin'
            }
            config = {
                'region': 'eu-central-1',
                'instance_type': 'm5.large',
                'keep_running': 'false',
                'ssh': ssh_config
            }
        elif iaas == 'azure':
            ssh_config = {
                'user': 'azureuser'
            }
            config = {
                'location': 'westeurope',
                'keep_running': 'false',
                'ssh': ssh_config
            }
            pass
        elif iaas == 'gcp':
            pass
        elif iaas == 'ali':
            pass
        elif iaas == 'openstack-ccee':
            pass
        return config


@pytest.fixture(scope="session")
def aws_session(testconfig, pipeline, request):
    import boto3
    
    if pipeline:
        import ccc.aws

        @dataclass
        class AWSCfg:
            aws_cfg_name: str
            aws_region: str

        test_params=request.getfixturevalue('test_params')
        cicd_cfg = glci.util.cicd_cfg(cfg_name=test_params.cicd_cfg_name)
        aws_cfg = AWSCfg(
            aws_cfg_name=cicd_cfg.build.aws_cfg_name,
            aws_region=cicd_cfg.build.aws_region
        )
        return ccc.aws.session(aws_cfg.aws_cfg_name, aws_cfg.aws_region)
    elif "region" in testconfig:
        return boto3.Session(region_name=testconfig["region"])
    else:
        return boto3.Session()


@pytest.fixture(scope="session")
def azure_cfg():

    @dataclass
    class AzureCfg:
        client_id: str
        client_secret: str
        tenant_id: str
        subscription_id: str
        marketplace_cfg: glci.model.AzureMarketplaceCfg

    cicd_cfg = glci.util.cicd_cfg()
    service_principal_cfg_tmp = ctx().cfg_factory().azure_service_principal(
        cicd_cfg.publish.azure.service_principal_cfg_name,
    )
    service_principal_cfg = glci.model.AzureServicePrincipalCfg(
        **service_principal_cfg_tmp.raw
    )

    azure_marketplace_cfg = glci.model.AzureMarketplaceCfg(
        publisher_id=cicd_cfg.publish.azure.publisher_id,
        offer_id=cicd_cfg.publish.azure.offer_id,
        plan_id=cicd_cfg.publish.azure.plan_id,
    )

    return AzureCfg(
        client_id=service_principal_cfg.client_id,
        client_secret=service_principal_cfg.client_secret,
        tenant_id=service_principal_cfg.tenant_id,
        marketplace_cfg=azure_marketplace_cfg,
        subscription_id=service_principal_cfg.subscription_id,
    )


@pytest.fixture(scope="session")
def azure_credentials(testconfig, pipeline, request):
    from azure.identity import (
        AzureCliCredential,
        ClientSecretCredential
    )

    @dataclass
    class AZCredentials:
        credential: object
        subscription_id: str

    if pipeline:
        azure_cfg = request.getfixturevalue('azure_cfg')
        credentials = ClientSecretCredential(
            client_id=azure_cfg.client_id,
            client_secret=azure_cfg.client_secret,
            tenant_id=azure_cfg.tenant_id
        )
        return AZCredentials(
            credential = credentials,
            subscription_id = azure_cfg.subscription_id
        )
    else:
        credential = AzureCliCredential()
        if 'subscription_id' in testconfig:
            subscription_id = testconfig['subscription_id']
        elif 'subscription' in testconfig:
            try:
                subscription_id = AZURE.find_subscription_id(credential, testconfig['subscription'])
            except RuntimeError as err:
                pytest.exit(err, 1)
        return AZCredentials(
            credential = credential,
            subscription_id = subscription_id
        )


@pytest.fixture(scope="module")
def client(testconfig, iaas, imageurl, request) -> Iterator[RemoteClient]:
    logger.info(f"Testconfig for {iaas=} is {testconfig}")
    if iaas == "aws":
        session = request.getfixturevalue('aws_session')
        yield from AWS.fixture(session, testconfig, imageurl)
    elif iaas == "gcp":
        yield from GCP.fixture(config["gcp"])
    elif iaas == "azure":
        credentials = request.getfixturevalue('azure_credentials')
        yield from AZURE.fixture(credentials, testconfig, imageurl)
    elif iaas == "openstack-ccee":
        yield from OpenStackCCEE.fixture(config["openstack_ccee"])
    elif iaas == "ali":
        yield from ALI.fixture(config["ali"])
    elif iaas == "manual":
        yield from Manual.fixture(config["manual"])
    else:
        raise ValueError(f"invalid {iaas=}")