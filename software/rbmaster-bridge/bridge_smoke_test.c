#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>

typedef DWORD (WINAPI *FT_CREATE_DEVICE_INFO_LIST)(DWORD *count);
typedef DWORD (WINAPI *FT_GET_DEVICE_INFO_DETAIL)(DWORD index, DWORD *flags,
    DWORD *type, DWORD *id, DWORD *loc, char *serial, char *description,
    void **handle);
typedef DWORD (WINAPI *FT_OPEN)(int index, void **handle);
typedef DWORD (WINAPI *FT_CLOSE)(void *handle);

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
    if (!create_list || !get_detail || !open_device || !close_device) {
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
        DWORD close_status = close_device(handle);
        printf("close status=%lu\n", close_status);
    }

    FreeLibrary(dll);
    return status == 0 ? 0 : 3;
}
