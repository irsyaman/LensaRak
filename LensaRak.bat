@echo off
setlocal
title LensaRak

rem ---------------------------------------------------------------------
rem Edit these two lines once to match your setup -- everything below
rem uses them automatically so you never type long commands by hand.
rem ---------------------------------------------------------------------
set ACTOR=Aiman
set LOCATION=STORE 1

rem Optional: uncomment and paste your Gemini key here ONLY on your own
rem laptop (never share this file once the key is filled in, and never
rem commit/submit it with the key still in it).
rem set GEMINI_API_KEY=paste_your_key_here

:menu
cls
echo ================================================
echo               LensaRak - Vision System
echo ================================================
echo.
echo   1. Check-out items       (webcam, live)
echo   2. Check-out items       (phone camera, live)
echo   3. Bulk return           (webcam, live)
echo   4. Bulk return           (phone camera, live)
echo   5. Review pending items  (no camera needed)
echo   6. View transaction history (no camera needed)
echo   7. Export full ledger to Excel/CSV (no camera needed)
echo   8. Exit
echo.
set /p choice=Choose (1-8):

if "%choice%"=="1" goto checkout_webcam
if "%choice%"=="2" goto checkout_phone
if "%choice%"=="3" goto bulkreturn_webcam
if "%choice%"=="4" goto bulkreturn_phone
if "%choice%"=="5" goto review
if "%choice%"=="6" goto history
if "%choice%"=="7" goto export
if "%choice%"=="8" goto end
echo.
echo Invalid choice, try again.
pause >nul
goto menu

:checkout_webcam
python lensarak_vision.py --source webcam --mode check_out --live --actor "%ACTOR%" --location "%LOCATION%"
goto done

:checkout_phone
set /p phoneurl=Enter phone stream URL (e.g. http://192.168.0.6:8081/video):
set rotate=0
set /p rotate=If the preview looks sideways, enter rotation 90/180/270 (blank = 0, no rotation):
if "%rotate%"=="" set rotate=0
python lensarak_vision.py --source phone --arg "%phoneurl%" --phone-rotate %rotate% --mode check_out --live --actor "%ACTOR%" --location "%LOCATION%"
goto done

:bulkreturn_webcam
python lensarak_vision.py --source webcam --mode bulk_return --live --actor "%ACTOR%" --location "%LOCATION%"
goto done

:bulkreturn_phone
set /p phoneurl=Enter phone stream URL (e.g. http://192.168.0.6:8081/video):
set rotate=0
set /p rotate=If the preview looks sideways, enter rotation 90/180/270 (blank = 0, no rotation):
if "%rotate%"=="" set rotate=0
python lensarak_vision.py --source phone --arg "%phoneurl%" --phone-rotate %rotate% --mode bulk_return --live --actor "%ACTOR%" --location "%LOCATION%"
goto done

:review
python lensarak_review.py
goto done

:history
python lensarak_history.py
goto done

:export
python lensarak_export.py
goto done

:done
echo.
echo Done. Press any key to return to the menu.
pause >nul
goto menu

:end
endlocal
