rm -rf ./organized
rm -f devices.json
python reorganize.py ./Exports ./organized --dry 0 --verbose
python create_index.py --output devices.json ./organized