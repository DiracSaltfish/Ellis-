<script setup lang="ts">
import { computed, ref } from 'vue'
import { postContactMessage } from '../lib/api'

const emit = defineEmits<{
  (event: 'back-home'): void
}>()

const email = ref('')
const message = ref('')
const submitting = ref(false)
const error = ref('')
const success = ref('')
const messageLength = computed(() => message.value.length)

async function submit() {
  if (submitting.value) return
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    await postContactMessage({
      email: email.value,
      message: message.value,
    })
    success.value = '留言已发送，管理员看到后会通过你留下的邮箱联系你。'
    message.value = ''
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="section contact-page">
    <div class="detail-panel contact-panel">
      <div class="contact-header">
        <div>
          <p class="eyebrow">联系管理员</p>
          <h2>有问题，给网站管理员留言</h2>
          <p class="model-note">只需要留下邮箱和问题描述。</p>
        </div>
        <button type="button" class="secondary-action" @click="emit('back-home')">返回首页</button>
      </div>

      <form class="contact-form" @submit.prevent="submit">
        <label class="contact-field">
          <span>邮箱</span>
          <input
            v-model.trim="email"
            type="email"
            inputmode="email"
            autocomplete="email"
            maxlength="320"
            placeholder="you@example.com"
            required
          />
        </label>

        <label class="contact-field">
          <span>留言内容</span>
          <textarea
            v-model="message"
            rows="8"
            maxlength="4000"
            placeholder="请描述你遇到的问题、出现场景，以及希望管理员如何联系你。"
            required
          />
        </label>

        <div class="contact-actions">
          <button type="submit" :disabled="submitting">
            {{ submitting ? '发送中...' : '提交留言' }}
          </button>
          <span class="contact-counter">{{ messageLength }}/4000</span>
        </div>
      </form>

      <p v-if="error" class="notice error">{{ error }}</p>
      <p v-else-if="success" class="notice ok">{{ success }}</p>
    </div>
  </section>
</template>
