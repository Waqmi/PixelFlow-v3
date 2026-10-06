Unicode true
SilentInstall silent
RequestExecutionLevel user
SetCompressor /SOLID lzma

OutFile "${OUTPUT_FILE}"

!ifndef ICON_FILE
  !error "ICON_FILE is required"
!endif
Icon "${ICON_FILE}"

Section
  GetTempFileName $0
  Delete "$0"
  CreateDirectory "$0"
  SetOutPath "$0"
  File /r "${PORTABLE_DIR}\*.*"
  ExecWait '"$0\PixelFlow.exe"' $1
  SetOutPath "$TEMP"
  RMDir /r "$0"
SectionEnd
