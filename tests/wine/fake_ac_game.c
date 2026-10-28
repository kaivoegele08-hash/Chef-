/* Test-"Spiel": legt die Assetto-Corsa-Mappings an und schreibt plausible Werte (für Wine-Integrationstest). */
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

static void *mk(const char *name, DWORD size) {
    HANDLE h = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, size, name);
    if (!h) { fprintf(stderr, "CreateFileMapping %s failed\n", name); exit(1); }
    return MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, size);
}
static void wput(char *base, int off, const wchar_t *s) { memcpy(base + off, s, (wcslen(s) + 1) * 2); }

int main(int argc, char **argv) {
    int seconds = argc > 1 ? atoi(argv[1]) : 10;
    char *phy = (char *)mk("Local\\acpmf_physics", 2048);
    char *gfx = (char *)mk("Local\\acpmf_graphics", 2048);
    char *sta = (char *)mk("Local\\acpmf_static", 2048);
    wput(sta, 0, L"1.7"); wput(sta, 30, L"1.16.4");
    *(int *)(sta + 64) = 24;                  /* numCars */
    wput(sta, 68, L"ks_ferrari_488_gt3");     /* carModel */
    wput(sta, 134, L"ks_monza");              /* track */
    wput(sta, 200, L"Kai"); wput(sta, 266, L"Test");
    *(int *)(sta + 412) = 8000;               /* maxRpm */
    *(float *)(sta + 416) = 120.0f;           /* maxFuel */
    *(float *)(sta + 520) = 5758.0f;          /* trackSPlineLength */
    wput(sta, 524, L"");                      /* trackConfiguration */
    for (int i = 0; i < seconds * 100; i++) {
        float t = i / 100.0f;
        *(int *)(phy + 0) = i;                /* packetId */
        *(float *)(phy + 4) = 1.0f;           /* gas */
        *(float *)(phy + 12) = 80.0f - t * 0.05f; /* fuel */
        *(int *)(phy + 16) = 6;               /* gear 5 */
        *(int *)(phy + 20) = 7200;
        *(float *)(phy + 28) = 245.0f;        /* speedKmh */
        for (int w = 0; w < 4; w++) {
            *(float *)(phy + 88 + w * 4) = 27.5f;   /* psi */
            *(float *)(phy + 120 + w * 4) = 99.0f;  /* tyreWear */
            *(float *)(phy + 152 + w * 4) = 85.0f;  /* core */
            *(float *)(phy + 368 + w * 4) = 90.0f;  /* I */
            *(float *)(phy + 384 + w * 4) = 88.0f;  /* M */
            *(float *)(phy + 400 + w * 4) = 86.0f;  /* O */
        }
        *(int *)(gfx + 0) = i;
        *(int *)(gfx + 4) = 2;                /* AC_LIVE */
        *(int *)(gfx + 8) = 2;                /* race */
        *(int *)(gfx + 132) = 3;              /* completedLaps */
        *(int *)(gfx + 136) = 5;              /* position */
        *(int *)(gfx + 140) = 30000 + i * 10; /* iCurrentTime */
        *(int *)(gfx + 144) = 107345;         /* iLastTime */
        *(int *)(gfx + 148) = 106900;         /* iBestTime */
        *(int *)(gfx + 172) = 20;             /* numberOfLaps */
        wput(gfx, 176, L"Medium");
        *(float *)(gfx + 248) = 0.25f + t * 0.001f; /* normalizedCarPosition */
        Sleep(10);
    }
    return 0;
}
