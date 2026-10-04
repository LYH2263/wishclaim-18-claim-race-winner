<template>
  <span class="pin" :data-state="pin?.state || 'open'">{{ pin?.label || '可认领' }}</span>
</template>
<script setup>
// 三路同钉: wall card, detail and mine all render this same pin.
defineProps({ pin: Object })
</script>
