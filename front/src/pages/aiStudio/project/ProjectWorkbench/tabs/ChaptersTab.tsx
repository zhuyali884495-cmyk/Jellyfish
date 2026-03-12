import { useState, useEffect } from 'react'
import { Card, Button, Tag, Space, Table, Empty, Modal, Input, message } from 'antd'
import type { TableColumnsType } from 'antd'
import { PlusOutlined, VideoCameraOutlined } from '@ant-design/icons'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { StudioChaptersService } from '../../../../../services/generated'
import { chapterStatusMap } from '../constants'
import { getChapterPrepPath, getChapterStudioPath } from '../routes'
import { useChapters, newId, type Chapter } from '../hooks/useProjectData'

const { TextArea } = Input
const CREATE_PARAM = 'create'

export function ChaptersTab() {
  const navigate = useNavigate()
  const { projectId } = useParams<{ projectId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const { chapters, loading, refresh, patchChapterLocal } = useChapters(projectId)

  const [editOpen, setEditOpen] = useState(false)
  const [editingChapter, setEditingChapter] = useState<Chapter | null>(null)
  const [editContent, setEditContent] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [createTitle, setCreateTitle] = useState('')
  const [createContent, setCreateContent] = useState('')

  const createParam = searchParams.get(CREATE_PARAM)
  useEffect(() => {
    if (createParam === '1') {
      setCreateOpen(true)
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.delete(CREATE_PARAM)
          return next
        },
        { replace: true }
      )
    }
  }, [createParam, setSearchParams])

  const openEditModal = (chapter: Chapter) => {
    setEditingChapter(chapter)
    setEditContent(chapter.rawText ?? '')
    setEditOpen(true)
  }

  const handleCreateChapter = async () => {
    if (!createTitle.trim()) {
      message.warning('请输入章节标题')
      return
    }
    if (!projectId) return
    try {
      const nextIndex = Math.max(0, ...chapters.map((c) => c.index)) + 1
      await StudioChaptersService.createChapterApiV1StudioChaptersPost({
        requestBody: {
          id: newId('c'),
          project_id: projectId,
          index: nextIndex,
          title: createTitle.trim(),
          summary: '',
          raw_text: createContent || undefined,
          storyboard_count: 0,
          status: 'draft',
        },
      })
      message.success('章节创建成功')
      setCreateOpen(false)
      setCreateTitle('')
      setCreateContent('')
      await refresh()
    } catch {
      message.error('创建章节失败')
    }
  }

  const useMock = import.meta.env.VITE_USE_MOCK === 'true'
  const handleCreateChapterMock = () => {
    if (!createTitle.trim()) {
      message.warning('请输入章节标题')
      return
    }
    message.success('创建成功（Mock）')
    setCreateOpen(false)
    setCreateTitle('')
    setCreateContent('')
    void refresh()
  }

  const columns: TableColumnsType<Chapter> = [
    { title: '章节', dataIndex: 'index', key: 'index', width: 80, render: (v: number) => `第${v}集` },
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
    { title: '分镜数', dataIndex: 'storyboardCount', key: 'storyboardCount', width: 90 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: Chapter['status']) => (
        <Tag color={chapterStatusMap[status].color}>{chapterStatusMap[status].text}</Tag>
      ),
    },
    { title: '更新时间', dataIndex: 'updatedAt', key: 'updatedAt', width: 160 },
    {
      title: '操作',
      key: 'action',
      width: 260,
      render: (_, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => openEditModal(record)}>
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => projectId && navigate(getChapterPrepPath(projectId, record.id))}
          >
            章节编辑
          </Button>
          <Button
            type="link"
            size="small"
            icon={<VideoCameraOutlined />}
            onClick={() => projectId && navigate(getChapterStudioPath(projectId, record.id))}
          >
            进入拍摄
          </Button>
        </Space>
      ),
    },
  ]

  if (chapters.length === 0 && !loading) {
    return (
      <>
        <Card>
          <Empty description="还没有任何章节，立即创建第一章吧" image={Empty.PRESENTED_IMAGE_SIMPLE}>
          <Space>
            <Button type="primary" size="large" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              创建第一章
            </Button>
          </Space>
        </Empty>
        </Card>
        <Modal
          title="新建章节"
          open={createOpen}
          onCancel={() => setCreateOpen(false)}
          onOk={useMock ? handleCreateChapterMock : handleCreateChapter}
          okText="创建"
          width={560}
        >
          <div className="space-y-3">
            <div>
              <span className="text-gray-600 text-sm">章节标题</span>
              <Input
                placeholder="例如：第1集 出租屋里的争吵"
                value={createTitle}
                onChange={(e) => setCreateTitle(e.target.value)}
                className="mt-1"
              />
            </div>
            <div>
              <span className="text-gray-600 text-sm">章节内容（可粘贴剧本）</span>
              <TextArea
                rows={6}
                placeholder="粘贴文学剧本..."
                value={createContent}
                onChange={(e) => setCreateContent(e.target.value)}
                className="mt-1 font-mono text-sm"
              />
            </div>
          </div>
        </Modal>
      </>
    )
  }

  return (
    <Card
      title="章节列表"
      extra={
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建章节
          </Button>
        </Space>
      }
    >
      <Table<Chapter>
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={chapters}
        pagination={{ pageSize: 10 }}
        size="small"
      />

      <Modal
        title={editingChapter ? `编辑章节：${editingChapter.title}` : '编辑章节'}
        open={editOpen}
        onCancel={() => {
          setEditOpen(false)
          setEditingChapter(null)
        }}
        onOk={async () => {
          if (!editingChapter) return
          if (useMock) {
            message.success('已保存（Mock）')
            setEditOpen(false)
            setEditingChapter(null)
            return
          }
          try {
            await StudioChaptersService.updateChapterApiV1StudioChaptersChapterIdPatch({
              chapterId: editingChapter.id,
              requestBody: {
                raw_text: editContent,
              },
            })
            patchChapterLocal(editingChapter.id, { rawText: editContent })
            message.success('已保存')
            setEditOpen(false)
            setEditingChapter(null)
            await refresh()
          } catch {
            message.error('保存失败')
          }
        }}
        okText="保存"
        width={720}
      >
        <div>
          <span className="text-gray-600 text-sm">原文</span>
          <TextArea
            rows={12}
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            placeholder="章节原文内容"
            className="mt-1 font-mono text-sm"
          />
        </div>
      </Modal>

      <Modal
        title="新建章节"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={useMock ? handleCreateChapterMock : handleCreateChapter}
        okText="创建"
        width={560}
      >
        <div className="space-y-3">
          <div>
            <span className="text-gray-600 text-sm">章节标题</span>
            <Input
              placeholder="例如：第1集 出租屋里的争吵"
              value={createTitle}
              onChange={(e) => setCreateTitle(e.target.value)}
              className="mt-1"
            />
          </div>
          <div>
            <span className="text-gray-600 text-sm">章节内容（可粘贴剧本）</span>
            <TextArea
              rows={6}
              placeholder="粘贴文学剧本..."
              value={createContent}
              onChange={(e) => setCreateContent(e.target.value)}
              className="mt-1 font-mono text-sm"
            />
          </div>
        </div>
      </Modal>
    </Card>
  )
}
