# Docker Compose Override Applier

This Umbrel app automatically applies `docker-compose.override.yml` files to their corresponding `docker-compose.yml` files across all apps in your `app-data` directory.

## What it does

- Scans all directories in `./app-data/*/` for Docker Compose files
- Identifies apps that have both `docker-compose.yml` and `docker-compose.override.yml`
- Merges the override configurations into the main compose files
- Creates automatic backups before making any changes
- Provides a web interface to monitor status and apply changes

## Features

- **Web Dashboard**: View the status of all apps and their compose files
- **Safe Operation**: Automatic backups are created before any modifications
- **Selective Processing**: Only applies overrides where both files exist
- **YAML Merging**: Properly merges YAML configurations with override precedence
- **API Endpoints**: Provides REST API for automation

## Usage

1. Install and run the app through your Umbrel interface
2. Access the web dashboard at the app's URL
3. Review the status of all your apps
4. Click "Apply All Overrides" to merge all applicable override files

## File Structure

The app expects the following structure in your `app-data` directory:

```
app-data/
├── app-name-1/
│   ├── docker-compose.yml          # Base configuration
│   └── docker-compose.override.yml # Override configuration
├── app-name-2/
│   ├── docker-compose.yml
│   └── docker-compose.override.yml
└── ...
```

## API Endpoints

- `GET /`: Web dashboard
- `GET /api/status`: JSON status of all apps
- `POST /api/apply`: Apply all overrides and return results

## Backup System

Before modifying any `docker-compose.yml` file, the app creates a backup with the format:
```
docker-compose.yml.backup.YYYYMMDD_HHMMSS
```

## Warning

⚠️ **Important**: This app modifies your Docker Compose configuration files. While backups are created automatically, ensure you understand what overrides you're applying before running the app.

## Troubleshooting

- Check the app logs if overrides aren't being applied correctly
- Verify that your YAML files are valid before applying overrides
- Look for backup files if you need to restore original configurations
