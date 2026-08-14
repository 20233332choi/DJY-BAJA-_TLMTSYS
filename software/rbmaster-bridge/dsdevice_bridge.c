#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

#define API __declspec(dllexport) DWORD WINAPI
#define FT_OK 0
#define FT_INVALID_HANDLE 1
#define FT_DEVICE_NOT_FOUND 2
#define FT_DEVICE_NOT_OPENED 3
#define FT_IO_ERROR 4
#define FT_INVALID_PARAMETER 6
#define FT_NOT_SUPPORTED 17

#define FT_PURGE_RX 1
#define FT_PURGE_TX 2
#define FT_LIST_NUMBER_ONLY 0x80000000UL
#define FT_LIST_BY_INDEX 0x40000000UL
#define FT_LIST_ALL 0x20000000UL

typedef void *FT_HANDLE;

typedef struct {
    DWORD Flags;
    DWORD Type;
    DWORD ID;
    DWORD LocId;
    char SerialNumber[16];
    char Description[64];
    FT_HANDLE ftHandle;
} FT_DEVICE_LIST_INFO_NODE;

static HANDLE g_com = INVALID_HANDLE_VALUE;
static DWORD g_read_timeout = 500;
static DWORD g_write_timeout = 500;
static DWORD g_baud = 9600;
static BYTE g_latency = 2;
static char g_port[32] = "COM8";
static char g_log_path[MAX_PATH] = "rb_esp32_bridge.log";
static CRITICAL_SECTION g_lock;
static FILE *g_log = NULL;

static void log_line(const char *format, ...) {
    va_list args;
    SYSTEMTIME st;
    GetLocalTime(&st);
    EnterCriticalSection(&g_lock);
    if (!g_log) {
        g_log = fopen(g_log_path, "a");
    }
    if (g_log) {
        fprintf(g_log, "%02u:%02u:%02u.%03u ", st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);
        va_start(args, format);
        vfprintf(g_log, format, args);
        va_end(args);
        fputc('\n', g_log);
        fflush(g_log);
    }
    LeaveCriticalSection(&g_lock);
}

static void log_hex(const char *label, const uint8_t *data, DWORD size) {
    char line[2048];
    int used = snprintf(line, sizeof(line), "%s %lu:", label, (unsigned long)size);
    DWORD limit = size > 512 ? 512 : size;
    for (DWORD i = 0; i < limit && used > 0 && used < (int)sizeof(line) - 4; ++i) {
        used += snprintf(line + used, sizeof(line) - (size_t)used, " %02X", data[i]);
    }
    if (size > limit && used > 0 && used < (int)sizeof(line) - 8) {
        snprintf(line + used, sizeof(line) - (size_t)used, " ...");
    }
    log_line("%s", line);
}

static int valid_handle(FT_HANDLE handle) {
    return g_com != INVALID_HANDLE_VALUE && handle == (FT_HANDLE)g_com;
}

static DWORD apply_timeouts(void) {
    COMMTIMEOUTS timeouts;
    memset(&timeouts, 0, sizeof(timeouts));
    timeouts.ReadIntervalTimeout = 1;
    timeouts.ReadTotalTimeoutConstant = g_read_timeout;
    timeouts.WriteTotalTimeoutConstant = g_write_timeout;
    return SetCommTimeouts(g_com, &timeouts) ? FT_OK : FT_IO_ERROR;
}

static DWORD apply_serial(DWORD baud, BYTE bits, BYTE stop, BYTE parity) {
    DCB dcb;
    memset(&dcb, 0, sizeof(dcb));
    dcb.DCBlength = sizeof(dcb);
    if (!GetCommState(g_com, &dcb)) return FT_IO_ERROR;
    dcb.BaudRate = baud;
    dcb.ByteSize = bits;
    dcb.Parity = parity;
    dcb.StopBits = stop;
    dcb.fBinary = TRUE;
    dcb.fParity = parity != NOPARITY;
    dcb.fOutxCtsFlow = FALSE;
    dcb.fOutxDsrFlow = FALSE;
    dcb.fDtrControl = DTR_CONTROL_DISABLE;
    dcb.fRtsControl = RTS_CONTROL_DISABLE;
    dcb.fOutX = FALSE;
    dcb.fInX = FALSE;
    return SetCommState(g_com, &dcb) ? FT_OK : FT_IO_ERROR;
}

static DWORD open_port(FT_HANDLE *out_handle) {
    if (!out_handle) return FT_INVALID_PARAMETER;
    if (g_com != INVALID_HANDLE_VALUE) {
        *out_handle = (FT_HANDLE)g_com;
        return FT_OK;
    }
    const char *configured = getenv("RB_ESP32_COM");
    if (configured && configured[0]) {
        strncpy(g_port, configured, sizeof(g_port) - 1);
        g_port[sizeof(g_port) - 1] = 0;
    }
    char device_path[64];
    snprintf(device_path, sizeof(device_path), "\\\\.\\%s", g_port);
    g_com = CreateFileA(device_path, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    if (g_com == INVALID_HANDLE_VALUE) {
        log_line("OPEN %s failed error=%lu", device_path, GetLastError());
        return FT_DEVICE_NOT_OPENED;
    }
    SetupComm(g_com, 65536, 65536);
    apply_serial(g_baud, 8, ONESTOPBIT, NOPARITY);
    apply_timeouts();
    PurgeComm(g_com, PURGE_RXCLEAR | PURGE_TXCLEAR);
    *out_handle = (FT_HANDLE)g_com;
    log_line("OPEN %s ok", device_path);
    return FT_OK;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)instance;
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DWORD length = GetModuleFileNameA(instance, g_log_path, sizeof(g_log_path));
        if (length > 0 && length < sizeof(g_log_path)) {
            char *separator = strrchr(g_log_path, '\\');
            if (separator) {
                strcpy(separator + 1, "rb_esp32_bridge.log");
            }
        } else {
            strcpy(g_log_path, "rb_esp32_bridge.log");
        }
        InitializeCriticalSection(&g_lock);
        log_line("DSDEVICE COM bridge loaded");
    } else if (reason == DLL_PROCESS_DETACH) {
        if (g_com != INVALID_HANDLE_VALUE) CloseHandle(g_com);
        if (g_log) fclose(g_log);
        DeleteCriticalSection(&g_lock);
    }
    return TRUE;
}

API FT_Open(int index, FT_HANDLE *handle) {
    log_line("FT_Open index=%d", index);
    return index == 0 ? open_port(handle) : FT_DEVICE_NOT_FOUND;
}

API FT_OpenEx(void *argument, DWORD flags, FT_HANDLE *handle) {
    log_line("FT_OpenEx flags=%lu arg=%s", flags, argument ? (const char *)argument : "(null)");
    return open_port(handle);
}

API FT_Close(FT_HANDLE handle) {
    log_line("FT_Close");
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    CloseHandle(g_com);
    g_com = INVALID_HANDLE_VALUE;
    return FT_OK;
}

API FT_Read(FT_HANDLE handle, void *buffer, DWORD requested, DWORD *read_count) {
    if (read_count) *read_count = 0;
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    DWORD got = 0;
    if (!ReadFile(g_com, buffer, requested, &got, NULL)) return FT_IO_ERROR;
    if (read_count) *read_count = got;
    if (got) log_hex("RX", (const uint8_t *)buffer, got);
    return FT_OK;
}

API FT_Write(FT_HANDLE handle, const void *buffer, DWORD requested, DWORD *write_count) {
    if (write_count) *write_count = 0;
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    DWORD sent = 0;
    log_hex("TX", (const uint8_t *)buffer, requested);
    if (!WriteFile(g_com, buffer, requested, &sent, NULL)) return FT_IO_ERROR;
    if (write_count) *write_count = sent;
    return sent == requested ? FT_OK : FT_IO_ERROR;
}

API FT_ResetDevice(FT_HANDLE handle) {
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    log_line("FT_ResetDevice");
    return PurgeComm(g_com, PURGE_RXCLEAR | PURGE_TXCLEAR) ? FT_OK : FT_IO_ERROR;
}

API FT_SetBaudRate(FT_HANDLE handle, DWORD baud) {
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    g_baud = baud;
    log_line("FT_SetBaudRate %lu", baud);
    return apply_serial(g_baud, 8, ONESTOPBIT, NOPARITY);
}

API FT_SetDivisor(FT_HANDLE handle, USHORT divisor) {
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    if (!divisor) return FT_INVALID_PARAMETER;
    return FT_SetBaudRate(handle, 3000000UL / divisor);
}

API FT_SetDataCharacteristics(FT_HANDLE handle, UCHAR bits, UCHAR stop, UCHAR parity) {
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    BYTE win_stop = stop == 2 ? TWOSTOPBITS : ONESTOPBIT;
    BYTE win_parity = parity <= SPACEPARITY ? parity : NOPARITY;
    log_line("FT_SetDataCharacteristics bits=%u stop=%u parity=%u", bits, stop, parity);
    return apply_serial(g_baud, bits, win_stop, win_parity);
}

API FT_SetFlowControl(FT_HANDLE handle, USHORT flow, UCHAR xon, UCHAR xoff) {
    (void)xon; (void)xoff;
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    log_line("FT_SetFlowControl %u", flow);
    return FT_OK;
}

API FT_SetTimeouts(FT_HANDLE handle, DWORD read_ms, DWORD write_ms) {
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    g_read_timeout = read_ms;
    g_write_timeout = write_ms;
    log_line("FT_SetTimeouts read=%lu write=%lu", read_ms, write_ms);
    return apply_timeouts();
}

API FT_Purge(FT_HANDLE handle, DWORD mask) {
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    DWORD flags = 0;
    if (mask & FT_PURGE_RX) flags |= PURGE_RXABORT | PURGE_RXCLEAR;
    if (mask & FT_PURGE_TX) flags |= PURGE_TXABORT | PURGE_TXCLEAR;
    log_line("FT_Purge mask=%lu", mask);
    return PurgeComm(g_com, flags) ? FT_OK : FT_IO_ERROR;
}

API FT_GetQueueStatus(FT_HANDLE handle, DWORD *rx_bytes) {
    if (!rx_bytes) return FT_INVALID_PARAMETER;
    *rx_bytes = 0;
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    COMSTAT stat;
    DWORD errors;
    if (!ClearCommError(g_com, &errors, &stat)) return FT_IO_ERROR;
    *rx_bytes = stat.cbInQue;
    return FT_OK;
}

API FT_GetStatus(FT_HANDLE handle, DWORD *rx, DWORD *tx, DWORD *events) {
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    COMSTAT stat;
    DWORD errors;
    if (!ClearCommError(g_com, &errors, &stat)) return FT_IO_ERROR;
    if (rx) *rx = stat.cbInQue;
    if (tx) *tx = stat.cbOutQue;
    if (events) *events = 0;
    return FT_OK;
}

API FT_SetDtr(FT_HANDLE handle) { return valid_handle(handle) && EscapeCommFunction(g_com, SETDTR) ? FT_OK : FT_IO_ERROR; }
API FT_ClrDtr(FT_HANDLE handle) { return valid_handle(handle) && EscapeCommFunction(g_com, CLRDTR) ? FT_OK : FT_IO_ERROR; }
API FT_SetRts(FT_HANDLE handle) { return valid_handle(handle) && EscapeCommFunction(g_com, SETRTS) ? FT_OK : FT_IO_ERROR; }
API FT_ClrRts(FT_HANDLE handle) { return valid_handle(handle) && EscapeCommFunction(g_com, CLRRTS) ? FT_OK : FT_IO_ERROR; }

API FT_GetModemStatus(FT_HANDLE handle, DWORD *status) {
    if (!valid_handle(handle) || !status) return FT_INVALID_HANDLE;
    return GetCommModemStatus(g_com, status) ? FT_OK : FT_IO_ERROR;
}

API FT_SetChars(FT_HANDLE h, UCHAR e, UCHAR ee, UCHAR er, UCHAR ere) { (void)e;(void)ee;(void)er;(void)ere; return valid_handle(h) ? FT_OK : FT_INVALID_HANDLE; }
API FT_SetLatencyTimer(FT_HANDLE h, UCHAR value) { if (!valid_handle(h)) return FT_INVALID_HANDLE; g_latency=value; return FT_OK; }
API FT_GetLatencyTimer(FT_HANDLE h, UCHAR *value) { if (!valid_handle(h)||!value) return FT_INVALID_HANDLE; *value=g_latency; return FT_OK; }
API FT_SetUSBParameters(FT_HANDLE h, DWORD in_sz, DWORD out_sz) { (void)in_sz;(void)out_sz; return valid_handle(h)?FT_OK:FT_INVALID_HANDLE; }
API FT_SetBitMode(FT_HANDLE h, UCHAR mask, UCHAR mode) { (void)mask;(void)mode; return valid_handle(h)?FT_OK:FT_INVALID_HANDLE; }
API FT_GetBitMode(FT_HANDLE h, UCHAR *mode) { if (!valid_handle(h)||!mode)return FT_INVALID_HANDLE; *mode=0; return FT_OK; }
API FT_SetBreakOn(FT_HANDLE h) { return valid_handle(h)&&SetCommBreak(g_com)?FT_OK:FT_IO_ERROR; }
API FT_SetBreakOff(FT_HANDLE h) { return valid_handle(h)&&ClearCommBreak(g_com)?FT_OK:FT_IO_ERROR; }
API FT_SetEventNotification(FT_HANDLE h, DWORD mask, void *event) { (void)mask;(void)event; return valid_handle(h)?FT_OK:FT_INVALID_HANDLE; }
API FT_GetEventStatus(FT_HANDLE h, DWORD *status) { if(!valid_handle(h)||!status)return FT_INVALID_HANDLE; *status=0; return FT_OK; }
API FT_SetWaitMask(FT_HANDLE h, DWORD mask) { (void)mask; return valid_handle(h)?FT_OK:FT_INVALID_HANDLE; }
API FT_WaitOnMask(FT_HANDLE h, DWORD *mask) { if(!valid_handle(h)||!mask)return FT_INVALID_HANDLE; *mask=0; return FT_OK; }
API FT_StopInTask(FT_HANDLE h) { return valid_handle(h)?FT_OK:FT_INVALID_HANDLE; }
API FT_RestartInTask(FT_HANDLE h) { return valid_handle(h)?FT_OK:FT_INVALID_HANDLE; }
API FT_SetResetPipeRetryCount(FT_HANDLE h, DWORD count) { (void)count; return valid_handle(h)?FT_OK:FT_INVALID_HANDLE; }
API FT_SetDeadmanTimeout(FT_HANDLE h, DWORD timeout) { (void)timeout; return valid_handle(h)?FT_OK:FT_INVALID_HANDLE; }
API FT_ResetPort(FT_HANDLE h) { return FT_ResetDevice(h); }
API FT_CyclePort(FT_HANDLE h) { return FT_ResetDevice(h); }

API FT_ListDevices(void *arg1, void *arg2, DWORD flags) {
    log_line("FT_ListDevices flags=0x%08lX", flags);
    if (flags & FT_LIST_NUMBER_ONLY) {
        if (!arg1) return FT_INVALID_PARAMETER;
        *(DWORD *)arg1 = 1;
        return FT_OK;
    }
    if (flags & FT_LIST_BY_INDEX) {
        if (!arg2 || (uintptr_t)arg1 != 0) return FT_DEVICE_NOT_FOUND;
        strcpy((char *)arg2, (flags & 1) ? "RBESP32" : "RAPID BIKE ECU");
        return FT_OK;
    }
    if (flags & FT_LIST_ALL) {
        if (arg2) *(DWORD *)arg2 = 1;
        if (arg1) strcpy(((char **)arg1)[0], (flags & 1) ? "RBESP32" : "RAPID BIKE ECU");
        return FT_OK;
    }
    return FT_INVALID_PARAMETER;
}

API FT_CreateDeviceInfoList(DWORD *count) {
    if (!count) return FT_INVALID_PARAMETER;
    *count = 1;
    log_line("FT_CreateDeviceInfoList -> 1");
    return FT_OK;
}

static void fill_device(FT_DEVICE_LIST_INFO_NODE *node) {
    memset(node, 0, sizeof(*node));
    node->Flags = g_com != INVALID_HANDLE_VALUE ? 1 : 0;
    node->Type = 5;
    node->ID = 0x0403E6F8UL;
    node->LocId = 1;
    strcpy(node->SerialNumber, "RBESP32");
    strcpy(node->Description, "RAPID BIKE ECU");
    node->ftHandle = g_com != INVALID_HANDLE_VALUE ? (FT_HANDLE)g_com : NULL;
}

API FT_GetDeviceInfoList(FT_DEVICE_LIST_INFO_NODE *dest, DWORD *count) {
    if (!dest || !count || *count < 1) return FT_INVALID_PARAMETER;
    fill_device(&dest[0]);
    *count = 1;
    return FT_OK;
}

API FT_GetDeviceInfoDetail(DWORD index, DWORD *flags, DWORD *type, DWORD *id, DWORD *loc,
                           char *serial, char *description, FT_HANDLE *handle) {
    if (index != 0) return FT_DEVICE_NOT_FOUND;
    FT_DEVICE_LIST_INFO_NODE node;
    fill_device(&node);
    if (flags) *flags=node.Flags;
    if (type) *type=node.Type;
    if (id) *id=node.ID;
    if (loc) *loc=node.LocId;
    if (serial) strcpy(serial,node.SerialNumber);
    if (description) strcpy(description,node.Description);
    if (handle) *handle=node.ftHandle;
    log_line("FT_GetDeviceInfoDetail index=0");
    return FT_OK;
}

API FT_GetDeviceInfo(FT_HANDLE h, DWORD *type, DWORD *id, char *serial, char *description, void *dummy) {
    (void)dummy;
    if (!valid_handle(h)) return FT_INVALID_HANDLE;
    if(type)*type=5; if(id)*id=0x0403E6F8UL;
    if(serial)strcpy(serial,"RBESP32"); if(description)strcpy(description,"RAPID BIKE ECU");
    return FT_OK;
}

API FT_GetDriverVersion(FT_HANDLE h, DWORD *version) { if(!valid_handle(h)||!version)return FT_INVALID_HANDLE; *version=0x02122414; return FT_OK; }
API FT_GetLibraryVersion(DWORD *version) { if(!version)return FT_INVALID_PARAMETER; *version=0x03022101; return FT_OK; }

API FT_IoCtl(FT_HANDLE h,DWORD code,void *in,DWORD in_len,void *out,DWORD out_len,DWORD *returned,LPOVERLAPPED ov) {
    (void)h;(void)code;(void)in;(void)in_len;(void)out;(void)out_len;(void)ov; if(returned)*returned=0; return FT_NOT_SUPPORTED;
}

API FT_EraseEE(FT_HANDLE h){(void)h;return FT_NOT_SUPPORTED;}
API FT_ReadEE(FT_HANDLE h,DWORD off,WORD *v){(void)h;(void)off;if(v)*v=0;return FT_NOT_SUPPORTED;}
API FT_WriteEE(FT_HANDLE h,DWORD off,WORD v){(void)h;(void)off;(void)v;return FT_NOT_SUPPORTED;}
API FT_EE_Program(FT_HANDLE h,void *d){(void)h;(void)d;return FT_NOT_SUPPORTED;}
API FT_EE_Read(FT_HANDLE h,void *d){(void)h;(void)d;return FT_NOT_SUPPORTED;}
API FT_EE_ProgramEx(FT_HANDLE h,void*d,char*m,char*mi,char*ds,char*sn){(void)h;(void)d;(void)m;(void)mi;(void)ds;(void)sn;return FT_NOT_SUPPORTED;}
API FT_EE_ReadEx(FT_HANDLE h,void*d,char*m,char*mi,char*ds,char*sn){(void)h;(void)d;(void)m;(void)mi;(void)ds;(void)sn;return FT_NOT_SUPPORTED;}
API FT_EE_UARead(FT_HANDLE h,UCHAR*d,DWORD n,DWORD*r){(void)h;(void)d;(void)n;if(r)*r=0;return FT_NOT_SUPPORTED;}
API FT_EE_UASize(FT_HANDLE h,DWORD*s){(void)h;if(s)*s=0;return FT_NOT_SUPPORTED;}
API FT_EE_UAWrite(FT_HANDLE h,UCHAR*d,DWORD n){(void)h;(void)d;(void)n;return FT_NOT_SUPPORTED;}

__declspec(dllexport) HANDLE WINAPI FT_W32_CreateFile(LPCTSTR n,DWORD a,DWORD s,LPSECURITY_ATTRIBUTES sa,DWORD c,DWORD f,HANDLE t){(void)n;(void)a;(void)s;(void)sa;(void)c;(void)f;(void)t;FT_HANDLE h=NULL;return open_port(&h)==FT_OK?(HANDLE)h:INVALID_HANDLE_VALUE;}
__declspec(dllexport) BOOL WINAPI FT_W32_CloseHandle(HANDLE h){return FT_Close((FT_HANDLE)h)==FT_OK;}
__declspec(dllexport) BOOL WINAPI FT_W32_ReadFile(HANDLE h,LPVOID b,DWORD n,LPDWORD r,LPOVERLAPPED o){(void)o;return FT_Read((FT_HANDLE)h,b,n,r)==FT_OK;}
__declspec(dllexport) BOOL WINAPI FT_W32_WriteFile(HANDLE h,LPCVOID b,DWORD n,LPDWORD w,LPOVERLAPPED o){(void)o;return FT_Write((FT_HANDLE)h,b,n,w)==FT_OK;}
__declspec(dllexport) BOOL WINAPI FT_W32_GetOverlappedResult(HANDLE h,LPOVERLAPPED o,LPDWORD n,BOOL wait){return GetOverlappedResult(h,o,n,wait);}
__declspec(dllexport) BOOL WINAPI FT_W32_ClearCommBreak(HANDLE h){return ClearCommBreak(h);}
__declspec(dllexport) BOOL WINAPI FT_W32_ClearCommError(HANDLE h,LPDWORD e,LPCOMSTAT s){return ClearCommError(h,e,s);}
__declspec(dllexport) BOOL WINAPI FT_W32_EscapeCommFunction(HANDLE h,DWORD f){return EscapeCommFunction(h,f);}
__declspec(dllexport) BOOL WINAPI FT_W32_GetCommModemStatus(HANDLE h,LPDWORD s){return GetCommModemStatus(h,s);}
__declspec(dllexport) BOOL WINAPI FT_W32_GetCommState(HANDLE h,LPDCB d){return GetCommState(h,d);}
__declspec(dllexport) BOOL WINAPI FT_W32_GetCommTimeouts(HANDLE h,LPCOMMTIMEOUTS t){return GetCommTimeouts(h,t);}
__declspec(dllexport) DWORD WINAPI FT_W32_GetLastError(void){return GetLastError();}
__declspec(dllexport) BOOL WINAPI FT_W32_PurgeComm(HANDLE h,DWORD f){return PurgeComm(h,f);}
__declspec(dllexport) BOOL WINAPI FT_W32_SetCommBreak(HANDLE h){return SetCommBreak(h);}
__declspec(dllexport) BOOL WINAPI FT_W32_SetCommMask(HANDLE h,DWORD m){return SetCommMask(h,m);}
__declspec(dllexport) BOOL WINAPI FT_W32_SetCommState(HANDLE h,LPDCB d){return SetCommState(h,d);}
__declspec(dllexport) BOOL WINAPI FT_W32_SetCommTimeouts(HANDLE h,LPCOMMTIMEOUTS t){return SetCommTimeouts(h,t);}
__declspec(dllexport) BOOL WINAPI FT_W32_SetupComm(HANDLE h,DWORD in,DWORD out){return SetupComm(h,in,out);}
__declspec(dllexport) BOOL WINAPI FT_W32_WaitCommEvent(HANDLE h,LPDWORD m,LPOVERLAPPED o){return WaitCommEvent(h,m,o);}
__declspec(dllexport) BOOL WINAPI FT_W32_CancelIo(HANDLE h){return CancelIo(h);}
