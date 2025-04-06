function setupEditorJs(editorJs) {
    const inputField = document.getElementById(editorJs.dataset.target);
    const config = editorJs.dataset.config ? JSON.parse(editorJs.dataset.config) : window.EditorJsDefaultConfig
    const csrfToken = document.querySelector('input[type="hidden"][name="csrfmiddlewaretoken"]').value
    
    if (Object.hasOwn(config, 'tools')) {
        for (const key in config.tools) {
            const tool = config.tools[key]

            if (Object.hasOwn(tool, 'class')) {
                tool.class = window[key]
            }

            if (Object.hasOwn(tool, 'config') && Object.hasOwn(tool.config, 'additionalRequestHeaders')) {
                if (Object.hasOwn(tool.config.additionalRequestHeaders, 'X-CSRFTOKEN')) {
                    tool.config.additionalRequestHeaders['X-CSRFTOKEN'] = csrfToken
                }
            }
        }
    }

    const editorInstance = new EditorJS({
        holder: editorJs.id,
        
        onChange: async () => {
            const outputData = await editorInstance.save()
            inputField.value = JSON.stringify(outputData)
        },

        data: JSON.parse(inputField.value),
        
        ...config,
    })
}

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".editor-js").forEach(setupEditorJs)
})
