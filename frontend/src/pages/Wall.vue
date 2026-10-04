<template>
  <div class="wall">
    <h1 class="serif">愿望墙</h1>
    <p class="tag">无顶栏 · 瀑布流 · 点卡片认领</p>
    <div class="masonry">
      <article v-for="w in rows" :key="w.id" class="card" @click="$router.push('/wishes/'+w.id)">
        <h3>{{ w.title || '（无标题）' }}</h3>
        <p>{{ w.note }}</p>
        <div class="card-foot">
          <span class="tag">{{ w.status }} · {{ w.data_quality }}</span>
          <span v-if="w.status === 'claimed'" class="pin">📌 {{ w.claimer }}</span>
        </div>
      </article>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const rows = ref([])
onMounted(async () => { rows.value = await api('/wishes') })
</script>
<style scoped>
.card-foot { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.pin {
  font-size: 12px; color: var(--ink); background: #f6d6d6;
  border-radius: 999px; padding: 2px 10px; white-space: nowrap;
}
</style>
