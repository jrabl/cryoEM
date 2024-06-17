#!/bin/bash
program_message='
------------------------Model-to-map-----------------------
Generate correctly centered maps from atomic models.
Resulting map can be imported to cryoSPARC and used
for mask generation.

This script uses EMAN2 and Relion for map generation,
shifting, and resizing.

Arguments: -i path to atomic model
           -a pixel size in angstrom
           -b box size in pixels

Example: bash mod2map.sh -i input_model.pdb -a 0.65 -b 512

Julius Rabl, ETH Zurich, 240717
-----------------------------------------------------------
'
printf "\n$program_message" 


inMod=''
Apix=''
boxSize=''

while getopts 'i:a:b:' flag; do
  case "${flag}" in
    i) inMod="${OPTARG}" ;;
    a) Apix="${OPTARG}" ;;
    b) boxSize="${OPTARG}" ;;
    *) error "Unexpected option ${flag}" ;;
  esac
done

echo 'Model input path: '$inMod 'Pixel size: '$Apix 'Box size: '$boxSize
echo 'Output of EMAN2 and relion below:'
echo '-----------------------------------------------------------'

let ShiftPixels=-boxSize/2
let InitialBox=2*boxSize

e2pdb2mrc.py $inMod intermediate.mrc --apix $Apix --res 10 --box $InitialBox
relion_image_handler --i intermediate.mrc --shift_x  $ShiftPixels --shift_y $ShiftPixels --shift_z $ShiftPixels --o intermediate2.mrc
relion_image_handler --i intermediate2.mrc --new_box $boxSize --o $inMod.mrc
rm -rf intermediate.mrc intermediate2.mrc

