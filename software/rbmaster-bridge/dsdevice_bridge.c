#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
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
#define RELAY_RESPONSE_TIMEOUT_MS 9000UL

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
static SOCKET g_socket = INVALID_SOCKET;
static int g_socket_mode = 0;
static uint8_t g_socket_rx[65536];
static DWORD g_socket_rx_size = 0;
static DWORD g_socket_rx_offset = 0;
static int g_socket_request_pending = 0;
static DWORD g_read_timeout = 500;
static DWORD g_write_timeout = 500;
static DWORD g_baud = 9600;
static BYTE g_latency = 2;
static char g_port[32] = "COM8";
static char g_log_path[MAX_PATH] = "rb_esp32_bridge.log";
static CRITICAL_SECTION g_lock;
static FILE *g_log = NULL;
static void log_line(const char *format, ...);

static int socket_send_all(const void *buffer, DWORD size) {
    const char *cursor = (const char *)buffer;
    DWORD sent = 0;
    while (sent < size) {
        int count = send(g_socket, cursor + sent, (int)(size - sent), 0);
        if (count <= 0) return 0;
        sent += (DWORD)count;
    }
    return 1;
}

static int socket_receive_all(void *buffer, DWORD size) {
    char *cursor = (char *)buffer;
    DWORD received = 0;
    while (received < size) {
        int count = recv(g_socket, cursor + received, (int)(size - received), 0);
        if (count <= 0) return 0;
        received += (DWORD)count;
    }
    return 1;
}

static int socket_receive_frame(void) {
    DWORD size = 0;
    if (!socket_receive_all(&size, sizeof(size))) return 0;
    if (size > sizeof(g_socket_rx)) return 0;
    if (size && !socket_receive_all(g_socket_rx, size)) return 0;
    g_socket_rx_size = size;
    g_socket_rx_offset = 0;
    g_socket_request_pending = 0;
    return 1;
}

static int socket_poll_frame(void) {
    u_long available = 0;
    if (g_socket_rx_offset < g_socket_rx_size) return 1;
    if (ioctlsocket(g_socket, FIONREAD, &available) != 0) return -1;
    if (available < sizeof(DWORD)) return 0;
    return socket_receive_frame() ? 1 : -1;
}

static void close_transport(void) {
    if (g_socket_mode) {
        if (g_socket != INVALID_SOCKET) closesocket(g_socket);
        g_socket = INVALID_SOCKET;
        WSACleanup();
    } else if (g_com != INVALID_HANDLE_VALUE) {
        CloseHandle(g_com);
    }
    g_socket_mode = 0;
    g_com = INVALID_HANDLE_VALUE;
    g_socket_rx_size = 0;
    g_socket_rx_offset = 0;
    g_socket_request_pending = 0;
}

static DWORD open_relay_socket(const char *endpoint, FT_HANDLE *out_handle) {
    char host[128];
    char port[16];
    const char *separator = strrchr(endpoint, ':');
    WSADATA data;
    struct addrinfo hints;
    struct addrinfo *addresses = NULL;
    struct addrinfo *address = NULL;
    if (!separator || separator == endpoint || !separator[1]) return FT_INVALID_PARAMETER;
    size_t host_length = (size_t)(separator - endpoint);
    if (host_length >= sizeof(host) || strlen(separator + 1) >= sizeof(port)) return FT_INVALID_PARAMETER;
    memcpy(host, endpoint, host_length);
    host[host_length] = 0;
    strcpy(port, separator + 1);
    if (WSAStartup(MAKEWORD(2, 2), &data) != 0) return FT_DEVICE_NOT_OPENED;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_protocol = IPPROTO_TCP;
    if (getaddrinfo(host, port, &hints, &addresses) != 0) {
        WSACleanup();
        return FT_DEVICE_NOT_OPENED;
    }
    for (address = addresses; address; address = address->ai_next) {
        g_socket = socket(address->ai_family, address->ai_socktype, address->ai_protocol);
        if (g_socket == INVALID_SOCKET) continue;
        if (connect(g_socket, address->ai_addr, (int)address->ai_addrlen) == 0) break;
        closesocket(g_socket);
        g_socket = INVALID_SOCKET;
    }
    freeaddrinfo(addresses);
    if (g_socket == INVALID_SOCKET) {
        WSACleanup();
        log_line("OPEN relay %s failed error=%d", endpoint, WSAGetLastError());
        return FT_DEVICE_NOT_OPENED;
    }
    g_socket_mode = 1;
    g_com = (HANDLE)(uintptr_t)1;
    g_socket_rx_size = 0;
    g_socket_rx_offset = 0;
    g_socket_request_pending = 0;
    *out_handle = (FT_HANDLE)g_com;
    log_line("OPEN relay %s ok", endpoint);
    return FT_OK;
}

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
    if (g_socket_mode) {
        DWORD read_timeout = g_read_timeout < RELAY_RESPONSE_TIMEOUT_MS
            ? RELAY_RESPONSE_TIMEOUT_MS : g_read_timeout;
        DWORD write_timeout = g_write_timeout;
        if (setsockopt(g_socket, SOL_SOCKET, SO_RCVTIMEO, (const char *)&read_timeout, sizeof(read_timeout)) != 0)
            return FT_IO_ERROR;
        if (setsockopt(g_socket, SOL_SOCKET, SO_SNDTIMEO, (const char *)&write_timeout, sizeof(write_timeout)) != 0)
            return FT_IO_ERROR;
        return FT_OK;
    }
    COMMTIMEOUTS timeouts;
    memset(&timeouts, 0, sizeof(timeouts));
    timeouts.ReadIntervalTimeout = 1;
    timeouts.ReadTotalTimeoutConstant = g_read_timeout;
    timeouts.WriteTotalTimeoutConstant = g_write_timeout;
    return SetCommTimeouts(g_com, &timeouts) ? FT_OK : FT_IO_ERROR;
}

static DWORD apply_serial(DWORD baud, BYTE bits, BYTE stop, BYTE parity) {
    if (g_socket_mode) {
        (void)baud; (void)bits; (void)stop; (void)parity;
        return FT_OK;
    }
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
    const char *relay = getenv("RB_ESP32_RELAY");
    if (relay && relay[0]) return open_relay_socket(relay, out_handle);
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
        close_transport();
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
    close_transport();
    return FT_OK;
}

API FT_Read(FT_HANDLE handle, void *buffer, DWORD requested, DWORD *read_count) {
    if (read_count) *read_count = 0;
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    DWORD got = 0;
    if (g_socket_mode) {
        if (g_socket_rx_offset >= g_socket_rx_size && !socket_receive_frame()) {
            g_socket_request_pending = 0;
            return FT_IO_ERROR;
        }
        DWORD available = g_socket_rx_size - g_socket_rx_offset;
        got = requested < available ? requested : available;
        if (got) memcpy(buffer, g_socket_rx + g_socket_rx_offset, got);
        g_socket_rx_offset += got;
        if (g_socket_rx_offset >= g_socket_rx_size) {
            g_socket_rx_size = 0;
            g_socket_rx_offset = 0;
        }
    } else if (!ReadFile(g_com, buffer, requested, &got, NULL)) {
        return FT_IO_ERROR;
    }
    if (read_count) *read_count = got;
    if (got) log_hex("RX", (const uint8_t *)buffer, got);
    return FT_OK;
}

API FT_Write(FT_HANDLE handle, const void *buffer, DWORD requested, DWORD *write_count) {
    if (write_count) *write_count = 0;
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    DWORD sent = 0;
    log_hex("TX", (const uint8_t *)buffer, requested);
    if (g_socket_mode) {
        if (!requested || requested > sizeof(g_socket_rx)) return FT_INVALID_PARAMETER;
        if (!socket_send_all(&requested, sizeof(requested)) || !socket_send_all(buffer, requested))
            return FT_IO_ERROR;
        g_socket_request_pending = 1;
        sent = requested;
    } else if (!WriteFile(g_com, buffer, requested, &sent, NULL)) {
        return FT_IO_ERROR;
    }
    if (write_count) *write_count = sent;
    return sent == requested ? FT_OK : FT_IO_ERROR;
}

API FT_ResetDevice(FT_HANDLE handle) {
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    log_line("FT_ResetDevice");
    if (g_socket_mode) {
        g_socket_rx_size = 0;
        g_socket_rx_offset = 0;
        return FT_OK;
    }
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
    if (g_socket_mode) {
        if (mask & FT_PURGE_RX) {
            g_socket_rx_size = 0;
            g_socket_rx_offset = 0;
        }
        log_line("FT_Purge relay mask=%lu", mask);
        return FT_OK;
    }
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
    if (g_socket_mode) {
        int polled = g_socket_request_pending ? socket_receive_frame() : socket_poll_frame();
        if (polled < 0) return FT_IO_ERROR;
        if (!polled && g_socket_request_pending) {
            g_socket_request_pending = 0;
            return FT_IO_ERROR;
        }
        *rx_bytes = g_socket_rx_size - g_socket_rx_offset;
        return FT_OK;
    }
    COMSTAT stat;
    DWORD errors;
    if (!ClearCommError(g_com, &errors, &stat)) return FT_IO_ERROR;
    *rx_bytes = stat.cbInQue;
    return FT_OK;
}

API FT_GetStatus(FT_HANDLE handle, DWORD *rx, DWORD *tx, DWORD *events) {
    if (!valid_handle(handle)) return FT_INVALID_HANDLE;
    if (g_socket_mode) {
        int polled = g_socket_request_pending ? socket_receive_frame() : socket_poll_frame();
        if (polled < 0) return FT_IO_ERROR;
        if (!polled && g_socket_request_pending) {
            g_socket_request_pending = 0;
            return FT_IO_ERROR;
        }
        if (rx) *rx = g_socket_rx_size - g_socket_rx_offset;
        if (tx) *tx = 0;
        if (events) *events = 0;
        return FT_OK;
    }
    COMSTAT stat;
    DWORD errors;
    if (!ClearCommError(g_com, &errors, &stat)) return FT_IO_ERROR;
    if (rx) *rx = stat.cbInQue;
    if (tx) *tx = stat.cbOutQue;
    if (events) *events = 0;
    return FT_OK;
}

API FT_SetDtr(FT_HANDLE handle) { return valid_handle(handle) && (g_socket_mode || EscapeCommFunction(g_com, SETDTR)) ? FT_OK : FT_IO_ERROR; }
API FT_ClrDtr(FT_HANDLE handle) { return valid_handle(handle) && (g_socket_mode || EscapeCommFunction(g_com, CLRDTR)) ? FT_OK : FT_IO_ERROR; }
API FT_SetRts(FT_HANDLE handle) { return valid_handle(handle) && (g_socket_mode || EscapeCommFunction(g_com, SETRTS)) ? FT_OK : FT_IO_ERROR; }
API FT_ClrRts(FT_HANDLE handle) { return valid_handle(handle) && (g_socket_mode || EscapeCommFunction(g_com, CLRRTS)) ? FT_OK : FT_IO_ERROR; }

API FT_GetModemStatus(FT_HANDLE handle, DWORD *status) {
    if (!valid_handle(handle) || !status) return FT_INVALID_HANDLE;
    if (g_socket_mode) {
        *status = 0;
        return FT_OK;
    }
    return GetCommModemStatus(g_com, status) ? FT_OK : FT_IO_ERROR;
}

API FT_SetChars(FT_HANDLE h, UCHAR e, UCHAR ee, UCHAR er, UCHAR ere) { (void)e;(void)ee;(void)er;(void)ere; return valid_handle(h) ? FT_OK : FT_INVALID_HANDLE; }
API FT_SetLatencyTimer(FT_HANDLE h, UCHAR value) { if (!valid_handle(h)) return FT_INVALID_HANDLE; g_latency=value; return FT_OK; }
API FT_GetLatencyTimer(FT_HANDLE h, UCHAR *value) { if (!valid_handle(h)||!value) return FT_INVALID_HANDLE; *value=g_latency; return FT_OK; }
API FT_SetUSBParameters(FT_HANDLE h, DWORD in_sz, DWORD out_sz) { (void)in_sz;(void)out_sz; return valid_handle(h)?FT_OK:FT_INVALID_HANDLE; }
API FT_SetBitMode(FT_HANDLE h, UCHAR mask, UCHAR mode) { (void)mask;(void)mode; return valid_handle(h)?FT_OK:FT_INVALID_HANDLE; }
API FT_GetBitMode(FT_HANDLE h, UCHAR *mode) { if (!valid_handle(h)||!mode)return FT_INVALID_HANDLE; *mode=0; return FT_OK; }
API FT_SetBreakOn(FT_HANDLE h) { return valid_handle(h)&&(g_socket_mode||SetCommBreak(g_com))?FT_OK:FT_IO_ERROR; }
API FT_SetBreakOff(FT_HANDLE h) { return valid_handle(h)&&(g_socket_mode||ClearCommBreak(g_com))?FT_OK:FT_IO_ERROR; }
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
__declspec(dllexport) BOOL WINAPI FT_W32_GetOverlappedResult(HANDLE h,LPOVERLAPPED o,LPDWORD n,BOOL wait){if(g_socket_mode){(void)h;(void)o;(void)wait;if(n)*n=0;return TRUE;}return GetOverlappedResult(h,o,n,wait);}
__declspec(dllexport) BOOL WINAPI FT_W32_ClearCommBreak(HANDLE h){return g_socket_mode?valid_handle((FT_HANDLE)h):ClearCommBreak(h);}
__declspec(dllexport) BOOL WINAPI FT_W32_ClearCommError(HANDLE h,LPDWORD e,LPCOMSTAT s){if(g_socket_mode){if(!valid_handle((FT_HANDLE)h)||socket_poll_frame()<0)return FALSE;if(e)*e=0;if(s){memset(s,0,sizeof(*s));s->cbInQue=g_socket_rx_size-g_socket_rx_offset;}return TRUE;}return ClearCommError(h,e,s);}
__declspec(dllexport) BOOL WINAPI FT_W32_EscapeCommFunction(HANDLE h,DWORD f){if(g_socket_mode){(void)f;return valid_handle((FT_HANDLE)h);}return EscapeCommFunction(h,f);}
__declspec(dllexport) BOOL WINAPI FT_W32_GetCommModemStatus(HANDLE h,LPDWORD s){if(g_socket_mode){if(!valid_handle((FT_HANDLE)h)||!s)return FALSE;*s=0;return TRUE;}return GetCommModemStatus(h,s);}
__declspec(dllexport) BOOL WINAPI FT_W32_GetCommState(HANDLE h,LPDCB d){if(g_socket_mode){if(!valid_handle((FT_HANDLE)h)||!d)return FALSE;memset(d,0,sizeof(*d));d->DCBlength=sizeof(*d);d->BaudRate=g_baud;d->ByteSize=8;d->StopBits=ONESTOPBIT;d->Parity=NOPARITY;d->fBinary=TRUE;return TRUE;}return GetCommState(h,d);}
__declspec(dllexport) BOOL WINAPI FT_W32_GetCommTimeouts(HANDLE h,LPCOMMTIMEOUTS t){if(g_socket_mode){if(!valid_handle((FT_HANDLE)h)||!t)return FALSE;memset(t,0,sizeof(*t));t->ReadIntervalTimeout=1;t->ReadTotalTimeoutConstant=g_read_timeout;t->WriteTotalTimeoutConstant=g_write_timeout;return TRUE;}return GetCommTimeouts(h,t);}
__declspec(dllexport) DWORD WINAPI FT_W32_GetLastError(void){return GetLastError();}
__declspec(dllexport) BOOL WINAPI FT_W32_PurgeComm(HANDLE h,DWORD f){if(g_socket_mode){DWORD mask=0;if(f&(PURGE_RXABORT|PURGE_RXCLEAR))mask|=FT_PURGE_RX;if(f&(PURGE_TXABORT|PURGE_TXCLEAR))mask|=FT_PURGE_TX;return FT_Purge((FT_HANDLE)h,mask)==FT_OK;}return PurgeComm(h,f);}
__declspec(dllexport) BOOL WINAPI FT_W32_SetCommBreak(HANDLE h){return g_socket_mode?valid_handle((FT_HANDLE)h):SetCommBreak(h);}
__declspec(dllexport) BOOL WINAPI FT_W32_SetCommMask(HANDLE h,DWORD m){if(g_socket_mode){(void)m;return valid_handle((FT_HANDLE)h);}return SetCommMask(h,m);}
__declspec(dllexport) BOOL WINAPI FT_W32_SetCommState(HANDLE h,LPDCB d){if(g_socket_mode){if(!valid_handle((FT_HANDLE)h)||!d)return FALSE;g_baud=d->BaudRate;return TRUE;}return SetCommState(h,d);}
__declspec(dllexport) BOOL WINAPI FT_W32_SetCommTimeouts(HANDLE h,LPCOMMTIMEOUTS t){if(g_socket_mode){if(!valid_handle((FT_HANDLE)h)||!t)return FALSE;g_read_timeout=t->ReadTotalTimeoutConstant;g_write_timeout=t->WriteTotalTimeoutConstant;return apply_timeouts()==FT_OK;}return SetCommTimeouts(h,t);}
__declspec(dllexport) BOOL WINAPI FT_W32_SetupComm(HANDLE h,DWORD in,DWORD out){if(g_socket_mode){(void)in;(void)out;return valid_handle((FT_HANDLE)h);}return SetupComm(h,in,out);}
__declspec(dllexport) BOOL WINAPI FT_W32_WaitCommEvent(HANDLE h,LPDWORD m,LPOVERLAPPED o){if(g_socket_mode){(void)o;if(!valid_handle((FT_HANDLE)h))return FALSE;if(m)*m=0;return TRUE;}return WaitCommEvent(h,m,o);}
__declspec(dllexport) BOOL WINAPI FT_W32_CancelIo(HANDLE h){return g_socket_mode?valid_handle((FT_HANDLE)h):CancelIo(h);}
