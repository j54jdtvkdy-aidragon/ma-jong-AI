/* 高速シャンテン/受け入れ計算。 gcc -O2 -shared -fPIC -o _fast.so _fast.c */
#include <string.h>

#include <stdint.h>

/* ---- 1色(9枚分)ごとの結果を遅延メモ化: tab[pattern][p*5+m] = 最大の搭子数(+2, 0=未計算, 1=不可能) ---- */
static uint8_t tab[1953125][10];
static int sc[9];
static int8_t sbest[10];

static void sdfs(int i, int m, int t, int p) {
    while (i < 9 && sc[i] == 0) i++;
    if (i >= 9) {
        int k = p * 5 + (m > 4 ? 4 : m);
        if (t > sbest[k]) sbest[k] = t;
        return;
    }
    if (sc[i] >= 3) { sc[i] -= 3; sdfs(i, m + 1, t, p); sc[i] += 3; }
    if (i <= 6 && sc[i + 1] && sc[i + 2]) {
        sc[i]--; sc[i + 1]--; sc[i + 2]--;
        sdfs(i, m + 1, t, p);
        sc[i]++; sc[i + 1]++; sc[i + 2]++;
    }
    if (sc[i] >= 2) {
        sc[i] -= 2;
        if (!p) sdfs(i, m, t, 1);
        sdfs(i, m, t + 1, p);
        sc[i] += 2;
    }
    if (i <= 7 && sc[i + 1]) { sc[i]--; sc[i + 1]--; sdfs(i, m, t + 1, p); sc[i]++; sc[i + 1]++; }
    if (i <= 6 && sc[i + 2]) { sc[i]--; sc[i + 2]--; sdfs(i, m, t + 1, p); sc[i]++; sc[i + 2]++; }
    sc[i]--; sdfs(i, m, t, p); sc[i]++;
}

static const uint8_t *suit_entry(const int *cnt9) {
    int key = 0;
    for (int i = 0; i < 9; i++) key = key * 5 + cnt9[i];
    uint8_t *e = tab[key];
    if (e[0] == 0 && e[1] == 0 && e[2] == 0 && e[3] == 0 && e[4] == 0 &&
        e[5] == 0 && e[6] == 0 && e[7] == 0 && e[8] == 0 && e[9] == 0) {
        memcpy(sc, cnt9, sizeof(int) * 9);
        for (int k = 0; k < 10; k++) sbest[k] = -1;
        sdfs(0, 0, 0, 0);
        for (int k = 0; k < 10; k++) e[k] = (uint8_t)(sbest[k] + 2);
    }
    return e;
}

int shanten_c(const int *cnt, int melds) {
    for (int i = 0; i < 34; i++) if (cnt[i] > 4 || cnt[i] < 0) return 9;   /* 不正な手牌 */
    /* dp[p*5+m] = これまでの色で達成できる最大の搭子数 (-1: 不可能) */
    int dp[10], nd[10];
    for (int k = 0; k < 10; k++) dp[k] = -1;
    dp[0] = 0;
    for (int s = 0; s < 3; s++) {
        const uint8_t *e = suit_entry(cnt + s * 9);
        for (int k = 0; k < 10; k++) nd[k] = -1;
        for (int p1 = 0; p1 < 2; p1++) for (int m1 = 0; m1 < 5; m1++) {
            int d = dp[p1 * 5 + m1];
            if (d < 0) continue;
            for (int p2 = 0; p2 + p1 < 2; p2++) for (int m2 = 0; m2 < 5; m2++) {
                int v = (int)e[p2 * 5 + m2] - 2;
                if (v < 0) continue;
                int m = m1 + m2; if (m > 4) m = 4;
                int k = (p1 + p2) * 5 + m;
                if (d + v > nd[k]) nd[k] = d + v;
            }
        }
        memcpy(dp, nd, sizeof(dp));
    }
    /* 字牌: 刻子=面子, 対子=搭子/雀頭 */
    int hm = 0, hp = 0;
    for (int i = 27; i < 34; i++) { if (cnt[i] >= 3) hm++; else if (cnt[i] == 2) hp++; }
    int best = 8;
    for (int p1 = 0; p1 < 2; p1++) for (int m1 = 0; m1 < 5; m1++) {
        int d = dp[p1 * 5 + m1];
        if (d < 0) continue;
        /* 字牌の対子は 雀頭 or 搭子 のどちらか */
        for (int hj = 0; hj <= (hp > 0 && !p1 ? 1 : 0); hj++) {
            int m = m1 + hm; if (m > 4) m = 4;
            int M = m + melds;
            int T = d + hp - hj;
            int lim = 4 - M; if (lim < 0) lim = 0;
            if (T > lim) T = lim;
            int s = 8 - 2 * M - T - (p1 + hj);
            if (s < best) best = s;
        }
    }
    if (melds == 0) {
        int pairs = 0, kinds = 0, k = 0, kp = 0;
        for (int i = 0; i < 34; i++) {
            if (cnt[i] >= 2) pairs++;
            if (cnt[i] >= 1) kinds++;
        }
        int s7 = 6 - pairs + (7 - kinds > 0 ? 7 - kinds : 0);
        if (s7 < best) best = s7;
        static const int term[13] = {0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33};
        for (int j = 0; j < 13; j++) { if (cnt[term[j]] >= 1) k++; if (cnt[term[j]] >= 2) kp = 1; }
        int sk = 13 - k - kp;
        if (sk < best) best = sk;
    }
    return best;
}

/* 14枚持ちの各打牌候補について、(打牌後シャンテン, 受け入れ枚数, 受け入れ牌ビットマスク)。
   visible: 自分から見えている34種カウント。手牌に無い牌は out_s=99。 */
void discard_table(const int *cnt14, int melds, const int *visible,
                   int *out_s, int *out_u, unsigned long long *out_mask) {
    int h[34];
    memcpy(h, cnt14, sizeof(int) * 34);
    for (int i = 0; i < 34; i++) {
        out_s[i] = 99; out_u[i] = 0; out_mask[i] = 0;
        if (!h[i]) continue;
        h[i]--;
        int s = shanten_c(h, melds);
        out_s[i] = s;
        if (s >= 0) {
            int u = 0; unsigned long long mk = 0;
            for (int t = 0; t < 34; t++) {
                int r = 4 - visible[t];
                if (r <= 0) continue;
                h[t]++;
                if (shanten_c(h, melds) < s) { u += r; mk |= 1ULL << t; }
                h[t]--;
            }
            out_u[i] = u; out_mask[i] = mk;
        }
        h[i]++;
    }
}

/* 13枚持ち(打牌済み)のシャンテンと受け入れ */
int ukeire_c(const int *cnt13, int melds, const int *visible, int *out_s, unsigned long long *out_mask) {
    int h[34];
    memcpy(h, cnt13, sizeof(int) * 34);
    int s = shanten_c(h, melds);
    *out_s = s; *out_mask = 0;
    if (s < 0) return 0;
    int u = 0; unsigned long long mk = 0;
    for (int t = 0; t < 34; t++) {
        int r = 4 - visible[t];
        if (r <= 0) continue;
        h[t]++;
        if (shanten_c(h, melds) < s) { u += r; mk |= 1ULL << t; }
        h[t]--;
    }
    *out_mask = mk;
    return u;
}
