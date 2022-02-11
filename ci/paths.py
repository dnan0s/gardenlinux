import os

own_dir = os.path.abspath(os.path.dirname(__file__))
repo_root = os.path.abspath(os.path.join(own_dir, os.pardir))

cicd_cfg_path = os.path.join(own_dir, 'cicd.yaml')
flavour_cfg_path = os.path.join(repo_root, 'flavours.yaml')
version_path = os.path.join(repo_root, 'VERSION')
version_cmd_path = os.path.join(repo_root, 'bin', 'garden-version')
