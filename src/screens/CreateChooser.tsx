import { useEffect } from 'react'
import { Link, useNavigate } from 'react-router'

/** Step 1 entry: enter the details by hand, or start from a transcript or document (mockup create-chooser.js). */
export default function CreateChooser() {
  const navigate = useNavigate()

  useEffect(() => {
    document.title = 'Create Project · NirnAIn'
  }, [])

  return (
    <section className="view on" id="v-create">
      <div className="pg narrow">
        <div className="crumb">
          <Link to="/dashboard">Dashboard</Link> &rsaquo; Create Project
        </div>
        <h1>Create New Project</h1>
        <div className="sub" style={{ marginBottom: 20 }}>
          How do you want to start?
        </div>
        <div className="choose">
          <div className="opt-card">
            <div className="ico" aria-hidden="true">
              &#9998;
            </div>
            <h2>Enter the details myself</h2>
            <p>Fill in the name, dates, budget and team. You can add meeting notes or files on the next step, AI Intake.</p>
            <ul>
              <li>Best when you already know the basics</li>
              <li>Four short sections; only two are required</li>
            </ul>
            <button className="btn" id="btn-start-manual" type="button" onClick={() => navigate('/projects/new/form')}>
              Start with the form &rarr;
            </button>
          </div>
          <div className="opt-card">
            <div className="ico" aria-hidden="true">
              &#10022;
            </div>
            <h2>Start from a meeting transcript or document</h2>
            <p>
              Upload a transcript, PDF or Word file, or paste notes. AI reads it, fills in the project details and pulls out the
              scope. You check everything before it&rsquo;s saved.
            </p>
            <ul>
              <li>Every value AI fills in shows the sentence it came from</li>
              <li>Anything it can&rsquo;t find is left blank for you, never guessed</li>
            </ul>
            <button className="btn" id="btn-start-upload" type="button" onClick={() => navigate('/projects/new/upload')}>
              Upload or paste &rarr;
            </button>
          </div>
        </div>
        <p className="small muted mt">Both paths end in the same place: a project with reviewed scope, ready for the WBS.</p>
      </div>
    </section>
  )
}
