#!/bin/bash

# Docker Compose Override Applier Script
# This script applies docker-compose.override.yml files to docker-compose.yml files

set -e

# Configuration
APP_DATA_DIR="${UMBREL_APP_DATA_DIR:-/umbrel-app-data}"
BACKUP_SUFFIX=".backup.$(date +%Y%m%d_%H%M%S)"

echo "Docker Compose Override Applier"
echo "==============================="
echo "App data directory: $APP_DATA_DIR"
echo ""

# Check if the directory exists
if [ ! -d "$APP_DATA_DIR" ]; then
    echo "Error: App data directory not found: $APP_DATA_DIR"
    exit 1
fi

# Function to merge YAML files using Python
merge_yaml() {
    local base_file="$1"
    local override_file="$2"
    local output_file="$3"
    
    python3 -c "
import yaml
import sys

def merge_yaml_configs(base_config, override_config):
    if not isinstance(base_config, dict) or not isinstance(override_config, dict):
        return override_config if override_config is not None else base_config
    
    result = base_config.copy()
    
    for key, value in override_config.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_yaml_configs(result[key], value)
        else:
            result[key] = value
    
    return result

try:
    with open('$base_file', 'r') as f:
        base_config = yaml.safe_load(f)
    
    with open('$override_file', 'r') as f:
        override_config = yaml.safe_load(f)
    
    merged_config = merge_yaml_configs(base_config, override_config)
    
    with open('$output_file', 'w') as f:
        yaml.dump(merged_config, f, default_flow_style=False, indent=2)
    
    print('SUCCESS')
except Exception as e:
    print(f'ERROR: {e}')
    sys.exit(1)
"
}

# Find and process all apps
total_processed=0
successful=0
errors=0

for app_dir in "$APP_DATA_DIR"/*; do
    if [ -d "$app_dir" ]; then
        app_name=$(basename "$app_dir")
        base_file="$app_dir/docker-compose.yml"
        override_file="$app_dir/docker-compose.override.yml"
        
        # Check if both files exist
        if [ -f "$base_file" ] && [ -f "$override_file" ]; then
            echo "Processing $app_name..."
            
            # Create backup
            backup_file="${base_file}${BACKUP_SUFFIX}"
            if cp "$base_file" "$backup_file"; then
                echo "  ✓ Backup created: $backup_file"
            else
                echo "  ✗ Failed to create backup for $app_name"
                ((errors++))
                continue
            fi
            
            # Apply override
            if result=$(merge_yaml "$base_file" "$override_file" "$base_file"); then
                if [ "$result" = "SUCCESS" ]; then
                    echo "  ✓ Override applied successfully"
                    ((successful++))
                else
                    echo "  ✗ Failed to apply override: $result"
                    # Restore backup
                    cp "$backup_file" "$base_file"
                    echo "  ✓ Original file restored from backup"
                    ((errors++))
                fi
            else
                echo "  ✗ Failed to apply override"
                # Restore backup
                cp "$backup_file" "$base_file"
                echo "  ✓ Original file restored from backup"
                ((errors++))
            fi
            
            ((total_processed++))
        else
            if [ -f "$base_file" ] && [ ! -f "$override_file" ]; then
                echo "Skipping $app_name (no override file)"
            elif [ ! -f "$base_file" ] && [ -f "$override_file" ]; then
                echo "Skipping $app_name (no base file)"
            fi
        fi
    fi
done

echo ""
echo "Summary:"
echo "========"
echo "Total processed: $total_processed"
echo "Successful: $successful"
echo "Errors: $errors"

if [ $errors -gt 0 ]; then
    exit 1
fi
