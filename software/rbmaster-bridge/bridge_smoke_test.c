#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef DWORD (WINAPI *FT_CREATE_DEVICE_INFO_LIST)(DWORD *count);
typedef DWORD (WINAPI *FT_GET_DEVICE_INFO_DETAIL)(DWORD index, DWORD *flags,
    DWORD *type, DWORD *id, DWORD *loc, char *serial, char *description,
    void **handle);
typedef DWORD (WINAPI *FT_OPEN)(int index, void **handle);
typedef DWORD (WINAPI *FT_CLOSE)(void *handle);
typedef DWORD (WINAPI *FT_WRITE)(void *handle, const void *buffer, DWORD size, DWORD *written);
typedef DWORD (WINAPI *FT_READ)(void *handle, void *buffer, DWORD size, DWORD *read_count);
typedef DWORD (WINAPI *FT_GET_QUEUE_STATUS)(void *handle, DWORD *rx_bytes);

int main(void) {
    HMODULE dll = LoadLibraryA("DSDEVICE.DLL");
    if (!dll) {
        printf("LoadLibrary failed: %lu\n", GetLastError());
        return 1;
    }

    FT_CREATE_DEVICE_INFO_LIST create_list =
        (FT_CREATE_DEVICE_INFO_LIST)GetProcAddress(dll, "FT_CreateDeviceInfoList");
    FT_GET_DEVICE_INFO_DETAIL get_detail =
        (FT_GET_DEVICE_INFO_DETAIL)GetProcAddress(dll, "FT_GetDeviceInfoDetail");
    FT_OPEN open_device = (FT_OPEN)GetProcAddress(dll, "FT_Open");
    FT_CLOSE close_device = (FT_CLOSE)GetProcAddress(dll, "FT_Close");
    FT_WRITE write_device = (FT_WRITE)GetProcAddress(dll, "FT_Write");
    FT_READ read_device = (FT_READ)GetProcAddress(dll, "FT_Read");
    FT_GET_QUEUE_STATUS get_queue = (FT_GET_QUEUE_STATUS)GetProcAddress(dll, "FT_GetQueueStatus");
    if (!create_list || !get_detail || !open_device || !close_device || !write_device || !read_device || !get_queue) {
        puts("Required export missing");
        FreeLibrary(dll);
        return 2;
    }

    DWORD count = 0;
    DWORD status = create_list(&count);
    printf("list status=%lu count=%lu\n", status, count);

    DWORD flags = 0, type = 0, id = 0, loc = 0;
    char serial[32] = {0};
    char description[64] = {0};
    void *detail_handle = NULL;
    status = get_detail(0, &flags, &type, &id, &loc, serial, description,
                        &detail_handle);
    printf("detail status=%lu type=%lu id=%08lX serial=%s description=%s\n",
           status, type, id, serial, description);

    void *handle = NULL;
    status = open_device(0, &handle);
    printf("open status=%lu handle=%p\n", status, handle);
    if (status == 0) {
        const char *relay_test = getenv("RB_ESP32_RELAY_TEST");
        if (relay_test && relay_test[0]) {
            const unsigned char request[] = {0xAA, 0x21, 0x55, 0x20};
            const unsigned char expected[] = {0x10, 0x20, 0x30};
            unsigned char response[sizeof(expected)] = {0};
            DWORD written = 0, received = 0;
            status = write_device(handle, request, sizeof(request), &written);
            printf("write status=%lu bytes=%lu\n", status, written);
            DWORD queued = 0;
            for (int attempt = 0; status == 0 && attempt < 200; ++attempt) {
                status = get_queue(handle, &queued);
                if (queued) break;
                Sleep(10);
            }
            printf("queue status=%lu bytes=%lu\n", status, queued);
            if (status == 0 && queued) status = read_device(handle, response, sizeof(response), &received);
            if (status == 0 && !queued) status = 5;
            printf("read status=%lu bytes=%lu\n", status, received);
            if (status == 0 && (received != sizeof(expected) || memcmp(response, expected, sizeof(expected)) != 0)) {
                puts("relay payload mismatch");
                status = 4;
            }
        }
        DWORD close_status = close_device(handle);
        printf("close status=%lu\n", close_status);
    }

    FreeLibrary(dll);
    return status == 0 ? 0 : 3;
}
