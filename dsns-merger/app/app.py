#!/usr/bin/env python3
"""
Docker Compose Override Applier
This app applies docker-compose.override.yml files to docker-compose.yml files
across all apps in the app-data directory.
"""

import os
import glob
import yaml
import logging
from flask import Flask, render_template, request, jsonify, redirect, url_for
from datetime import datetime
import subprocess
import shutil

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
UMBREL_APP_DATA_DIR = os.environ.get('UMBREL_APP_DATA_DIR', '/umbrel-app-data')

def merge_yaml_configs(base_config, override_config):
    """
    Merge two YAML configurations with override taking precedence.
    """
    if not isinstance(base_config, dict) or not isinstance(override_config, dict):
        return override_config if override_config is not None else base_config
    
    result = base_config.copy()
    
    for key, value in override_config.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_yaml_configs(result[key], value)
        else:
            result[key] = value
    
    return result

def find_compose_files():
    """
    Find all docker-compose.yml and docker-compose.override.yml files in app-data.
    """
    compose_files = {}
    
    # Search for all docker-compose files
    pattern = os.path.join(UMBREL_APP_DATA_DIR, '*', 'docker-compose*.yml')
    for file_path in glob.glob(pattern):
        app_name = os.path.basename(os.path.dirname(file_path))
        file_name = os.path.basename(file_path)
        
        if app_name not in compose_files:
            compose_files[app_name] = {}
        
        compose_files[app_name][file_name] = file_path
    
    return compose_files

def apply_overrides():
    """
    Apply docker-compose overrides to all applicable apps.
    """
    results = []
    compose_files = find_compose_files()
    
    for app_name, files in compose_files.items():
        base_file = files.get('docker-compose.yml')
        override_file = files.get('docker-compose.override.yml')
        
        if not base_file or not override_file:
            continue
        
        try:
            # Backup original file
            backup_file = f"{base_file}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(base_file, backup_file)
            
            # Load YAML files
            with open(base_file, 'r') as f:
                base_config = yaml.safe_load(f)
            
            with open(override_file, 'r') as f:
                override_config = yaml.safe_load(f)
            
            # Merge configurations
            merged_config = merge_yaml_configs(base_config, override_config)
            
            # Write merged configuration back to base file
            with open(base_file, 'w') as f:
                yaml.dump(merged_config, f, default_flow_style=False, indent=2)
            
            results.append({
                'app': app_name,
                'status': 'success',
                'message': f'Successfully applied override to {app_name}',
                'backup_file': backup_file
            })
            
            logger.info(f"Applied override for {app_name}")
            
        except Exception as e:
            results.append({
                'app': app_name,
                'status': 'error',
                'message': f'Error applying override to {app_name}: {str(e)}'
            })
            logger.error(f"Error applying override for {app_name}: {str(e)}")
    
    return results

def get_app_status():
    """
    Get status of all apps with docker-compose files.
    """
    compose_files = find_compose_files()
    apps = []
    
    for app_name, files in compose_files.items():
        has_base = 'docker-compose.yml' in files
        has_override = 'docker-compose.override.yml' in files
        
        apps.append({
            'name': app_name,
            'has_base': has_base,
            'has_override': has_override,
            'can_apply': has_base and has_override
        })
    
    return apps

@app.route('/')
def index():
    """Main dashboard."""
    apps = get_app_status()
    return render_template('index.html', apps=apps)

@app.route('/api/status')
def api_status():
    """API endpoint for app status."""
    apps = get_app_status()
    return jsonify(apps)

@app.route('/api/apply', methods=['POST'])
def api_apply():
    """API endpoint to apply overrides."""
    results = apply_overrides()
    return jsonify(results)

@app.route('/apply', methods=['POST'])
def apply():
    """Web endpoint to apply overrides."""
    results = apply_overrides()
    return render_template('results.html', results=results)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
